import os
import threading
import time
import uuid
from dataclasses import dataclass

from .skill_manager import SkillManager


@dataclass(frozen=True)
class SkillChangeEvent:
    event_id: str
    action: str
    skill_names: tuple
    source: str = "system"
    session_id: str = ""
    revision: int = 0

    @classmethod
    def create(cls, action, skill_names, source="system", session_id="", revision=0):
        names = tuple(dict.fromkeys(str(name or "").strip() for name in (skill_names or []) if str(name or "").strip()))
        return cls(uuid.uuid4().hex, str(action or "updated"), names, str(source or "system"), str(session_id or ""), int(revision or 0))

    def to_dict(self):
        return {
            "event_id": self.event_id,
            "action": self.action,
            "skill_names": list(self.skill_names),
            "source": self.source,
            "session_id": self.session_id,
            "revision": self.revision,
        }


class SkillCatalogSnapshot:
    """Immutable-by-convention process snapshot used to create cheap run views."""

    def __init__(self, revision, manager, created_at=None):
        self.revision = int(revision)
        self.manager = manager
        self.created_at = float(created_at or time.time())

    def runtime(self, workspace_dir=None, config_manager=None, dependency_coordinator=None, change_publisher=None):
        return self.manager.clone_for_runtime(
            workspace_dir,
            config_manager=config_manager,
            catalog_revision=self.revision,
            dependency_coordinator=dependency_coordinator,
            change_publisher=change_publisher,
        )


class SkillCatalogService:
    """Owns process-level Skill snapshots and swaps them atomically after changes."""

    def __init__(self, config_manager, workspace_dir=None, logger=None, dependency_coordinator=None):
        self.config_manager = config_manager
        self.workspace_dir = workspace_dir
        self.logger = logger
        self.dependency_coordinator = dependency_coordinator
        self._lock = threading.RLock()
        self._snapshot = None
        self._revision = 0
        self._listeners = []
        self._reload_lock = threading.RLock()
        self._applied_events = {}
        self._filesystem_signature = {}
        self._watch_stop = threading.Event()
        self._watch_thread = None

    def _log(self, message):
        if callable(self.logger):
            self.logger(message)

    def is_ready(self):
        with self._lock:
            return self._snapshot is not None

    def snapshot(self):
        with self._lock:
            if self._snapshot is None:
                raise RuntimeError("Skill catalog is not ready.")
            return self._snapshot

    def preload(self):
        return self.reload(reason="preload")

    def reload(self, reason="manual", required_event=None):
        started = time.time()
        with self._reload_lock:
            if hasattr(self.config_manager, "load_config"):
                self.config_manager.load_config()
            manager = SkillManager(
                self.workspace_dir,
                self.config_manager,
                auto_load=True,
                load_mcp_tools=False,
                prepare_dependencies=False,
                dependency_coordinator=self.dependency_coordinator,
            )
            if required_event is not None and required_event.source != "filesystem" and required_event.action in {
                "created",
                "updated",
                "enabled",
                "dependency_changed",
            }:
                for skill_name in required_event.skill_names:
                    record = manager.skill_records.get(skill_name)
                    if record is None:
                        raise RuntimeError(f"Skill '{skill_name}' was not present in the rebuilt catalog.")
                    if record.get("available") is False:
                        raise RuntimeError(
                            f"Skill '{skill_name}' is unavailable after validation: {record.get('load_error') or 'unknown load error'}"
                        )
            with self._lock:
                self._revision += 1
                snapshot = SkillCatalogSnapshot(self._revision, manager)
                self._snapshot = snapshot
                self._filesystem_signature = self._build_filesystem_signature(manager)
        self._log(
            f"skill_catalog reload_done revision={snapshot.revision} reason={reason} "
            f"skills={len(manager.skill_records)} duration={time.time() - started:.3f}s"
        )
        return snapshot

    def subscribe(self, callback):
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def unsubscribe(self, callback):
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    @staticmethod
    def _build_filesystem_signature(manager):
        signature = {}
        records = getattr(manager, "skill_records", {}) or {}
        for skills_dir in getattr(manager, "skills_dirs", []) or []:
            root = os.path.abspath(skills_dir)
            if not os.path.isdir(root):
                continue
            try:
                skill_names = os.listdir(root)
            except OSError:
                continue
            for skill_name in skill_names:
                skill_dir = os.path.join(root, skill_name)
                if not os.path.isdir(skill_dir) or skill_name.startswith("."):
                    continue
                candidates = [
                    os.path.join(skill_dir, "SKILL.md"),
                    os.path.join(skill_dir, "skill.json"),
                    os.path.join(skill_dir, "impl.py"),
                ]
                record = records.get(skill_name) or {}
                spec = record.get("spec") or {}
                for key in ("references", "script_refs", "asset_refs"):
                    candidates.extend(
                        os.path.join(skill_dir, str(relative_path))
                        for relative_path in spec.get(key) or []
                        if isinstance(relative_path, str) and relative_path.strip()
                    )
                experience_policy = spec.get("experience_policy") or {}
                entries_relative = experience_policy.get("entry_storage") or "experience/entries.jsonl"
                candidates.append(os.path.join(skill_dir, str(entries_relative)))
                for path in candidates:
                    try:
                        stat = os.stat(path)
                    except OSError:
                        continue
                    signature[path] = (stat.st_mtime_ns, stat.st_size, skill_name)
        return signature

    def start_watching(self, interval_seconds=1.0):
        with self._lock:
            if self._watch_thread and self._watch_thread.is_alive():
                return
            self._watch_stop.clear()
            self._watch_thread = threading.Thread(
                target=self._watch_loop,
                args=(max(float(interval_seconds or 1.0), 0.25),),
                name="skill-catalog-watcher",
                daemon=True,
            )
            self._watch_thread.start()

    def stop_watching(self):
        self._watch_stop.set()

    def _watch_loop(self, interval_seconds):
        while not self._watch_stop.wait(interval_seconds):
            try:
                with self._reload_lock:
                    snapshot = self.snapshot()
                    signature = self._build_filesystem_signature(snapshot.manager)
                    with self._lock:
                        previous = dict(self._filesystem_signature)
                    if signature == previous:
                        continue
                    changed_paths = set(signature).symmetric_difference(previous)
                    changed_paths.update(path for path in set(signature).intersection(previous) if signature[path] != previous[path])
                    changed_names = sorted(
                        {
                            (signature.get(path) or previous.get(path) or (None, None, ""))[2]
                            for path in changed_paths
                            if (signature.get(path) or previous.get(path) or (None, None, ""))[2]
                        }
                    )
                    self.publish_change(SkillChangeEvent.create("updated", changed_names, source="filesystem"))
            except Exception as exc:
                self._log(f"skill_catalog watcher_failed error={exc}")

    def publish_change(self, event):
        if not isinstance(event, SkillChangeEvent):
            event = SkillChangeEvent.create(
                (event or {}).get("action"),
                (event or {}).get("skill_names"),
                source=(event or {}).get("source"),
                session_id=(event or {}).get("session_id"),
                revision=(event or {}).get("revision"),
            )
        if event.action not in {"created", "updated", "enabled", "disabled", "deleted", "dependency_changed"}:
            raise ValueError(f"Unsupported Skill change action: {event.action}")
        with self._reload_lock:
            return self._publish_change_locked(event)

    def _publish_change_locked(self, event):
        with self._lock:
            prior = self._applied_events.get(event.event_id)
            if prior is not None:
                return prior
            if event.revision and event.revision <= self._revision:
                return SkillChangeEvent(
                    event.event_id,
                    event.action,
                    event.skill_names,
                    event.source,
                    event.session_id,
                    self._revision,
                )
        snapshot = self.reload(reason=f"change:{event.action}", required_event=event)
        applied = SkillChangeEvent(
            event.event_id,
            event.action,
            event.skill_names,
            event.source,
            event.session_id,
            snapshot.revision,
        )
        with self._lock:
            self._applied_events[applied.event_id] = applied
            if len(self._applied_events) > 2048:
                oldest = next(iter(self._applied_events))
                self._applied_events.pop(oldest, None)
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(applied, snapshot)
            except Exception as exc:
                self._log(f"skill_catalog listener_failed event_id={applied.event_id} error={exc}")
        return applied


class DependencyCoordinator:
    """Single-flight, first-use dependency preparation shared by a process."""

    def __init__(self, config_manager=None, logger=None):
        self.config_manager = config_manager
        self.logger = logger
        self._lock = threading.RLock()
        self._flights = {}

    def _timeout(self):
        value = self.config_manager.get("skill_dependency_install_timeout_seconds", 300) if self.config_manager else 300
        return max(30, min(int(value or 300), 1800))

    def _log(self, message):
        if callable(self.logger):
            self.logger(message)

    def ensure_ready(self, skill_name, python_dependencies=None, node_dependencies=None, retry=False, progress=None):
        from .sandbox_runtime import install_skill_dependencies, read_skill_dependency_status, skill_dependency_hash

        python_dependencies = list(python_dependencies or [])
        node_dependencies = list(node_dependencies or [])
        if not python_dependencies and not node_dependencies:
            return {"ok": True, "message": "No dependencies declared.", "installed": False}
        key = (str(skill_name), tuple(sorted(python_dependencies)), tuple(sorted(node_dependencies)))
        owner = False
        with self._lock:
            flight = self._flights.get(key)
            if flight is None:
                flight = {"event": threading.Event(), "result": None}
                self._flights[key] = flight
                owner = True
        if not owner:
            self._log(f"skill_dependency wait skill={skill_name}")
            if callable(progress):
                progress(f"正在等待能力 {skill_name} 的依赖准备完成…")
            flight["event"].wait(self._timeout() + 5)
            return flight.get("result") or {"ok": False, "message": f"Skill '{skill_name}' dependency preparation did not finish."}
        try:
            existing = read_skill_dependency_status(skill_name)
            expected_hash = skill_dependency_hash(python_dependencies, node_dependencies)
            same_hash = existing.get("hash") == expected_hash
            if existing.get("ok") and same_hash and not retry:
                result = existing
            elif existing and same_hash and not existing.get("ok") and not retry:
                result = existing
            else:
                started = time.time()
                self._log(f"skill_dependency install_start skill={skill_name} timeout={self._timeout()}")
                if callable(progress):
                    progress(f"正在准备能力 {skill_name} 的运行依赖…")
                result = install_skill_dependencies(
                    skill_name,
                    python_dependencies=python_dependencies,
                    node_dependencies=node_dependencies,
                    force=bool(retry),
                    timeout_seconds=self._timeout(),
                )
                self._log(
                    f"skill_dependency install_finish skill={skill_name} ok={bool(result.get('ok'))} "
                    f"duration={time.time() - started:.3f}s error={result.get('message') if not result.get('ok') else ''}"
                )
            flight["result"] = result
            return result
        finally:
            flight["event"].set()
            with self._lock:
                self._flights.pop(key, None)
