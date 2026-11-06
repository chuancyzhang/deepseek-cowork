"""Application-wide browser selection and run leases shared by desktop and daemon.

The saved preference is not an execution grant. A run pins a backend generation;
the first browser call acquires a lease, and a switch waits for all leases to end.
Only sessions created by that run are cleaned up. Never restart the shared bsk
daemon or infer ownership from its global session list.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

log = logging.getLogger(__name__)
CONFIG_KEY = "experimental_browser_automation"
INTERNAL = "internal"
EXTERNAL = "external"
WEB_TOOLS = frozenset({"browser_observe", "browser_navigate", "browser_action"})
SHARED_TOOLS = frozenset({"browser_tabs", "browser_open", "browser_close"})
INTERNAL_TOOLS = WEB_TOOLS | SHARED_TOOLS
_PROCESS_TOKEN = uuid.uuid4().hex
_binding_lock = threading.RLock()
_recovery_lock = threading.Lock()
_recovery_jobs = {}


class BrowserBackendError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def normalize_policy(value):
    # Missing preferences use the built-in browser. Preserve explicit choices.
    return {"enabled": value.get("enabled", True) is True if isinstance(value, dict) else True}


def _alive(pid):
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Access denied is not proof of exit.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class BrowserBackendCoordinator:
    def __init__(self, config=None):
        from .env_utils import get_app_data_dir
        self.config = config
        raw_path = getattr(config, "config_path", None)
        self.config_path = Path(raw_path) if isinstance(raw_path, (str, Path)) else None
        history = config.get_chat_history_dir() if config is not None and hasattr(config, "get_chat_history_dir") else None
        if config is not None and self.config_path is None and not isinstance(history, (str, Path)):
            raise BrowserBackendError("browser_config_unavailable", "浏览器配置目录不可用，无法建立运行状态。")
        self.root = self.config_path.parent if self.config_path else Path(history) if isinstance(history, (str, Path)) else Path(get_app_data_dir())
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "browser_backend.sqlite"
        with self._transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, backend TEXT NOT NULL, generation INTEGER NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS leases (session TEXT NOT NULL, run TEXT NOT NULL, backend TEXT NOT NULL, pid INTEGER NOT NULL, process_token TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0, finished INTEGER NOT NULL DEFAULT 0, sessions TEXT NOT NULL DEFAULT '[]', error TEXT NOT NULL DEFAULT '', PRIMARY KEY(session, run))")
            db.execute("CREATE TABLE IF NOT EXISTS finished_runs (session TEXT NOT NULL, run TEXT NOT NULL, PRIMARY KEY(session, run))")
            db.execute("INSERT OR IGNORE INTO state VALUES (1, ?, 0)", (self._desired(),))

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _desired(self):
        if self.config_path and self.config_path.exists():
            with self.config_path.open(encoding="utf-8") as stream:
                value = json.load(stream).get(CONFIG_KEY)
        elif self.config is not None:
            getter = getattr(self.config, "get", None)
            value = getter(CONFIG_KEY, {}) if callable(getter) else {}
        else:
            # The host continuation uses the same directory as ConfigManager.
            from .config_manager import ConfigManager
            self.config = ConfigManager()
            self.config_path = Path(self.config.config_path)
            value = self.config.get(CONFIG_KEY, {})
        return INTERNAL if normalize_policy(value)["enabled"] else EXTERNAL

    def _state(self, db):
        row = db.execute("SELECT * FROM state WHERE id=1").fetchone()
        desired = self._desired()
        count = db.execute("SELECT COUNT(*) FROM leases").fetchone()[0]
        if row["backend"] != desired and not count:
            db.execute("UPDATE state SET backend=?, generation=generation+1 WHERE id=1", (desired,))
            row = db.execute("SELECT * FROM state WHERE id=1").fetchone()
            log.info("browser.backend_switched backend=%s generation=%s", row["backend"], row["generation"])
        return {"backend": row["backend"], "generation": row["generation"], "desired": desired,
                "pending": row["backend"] != desired, "active_runs": count}

    def snapshot(self):
        with self._transaction() as db:
            result = self._state(db)
            rows = db.execute("SELECT * FROM leases").fetchall()
            result["cleanup_errors"] = [row["error"] for row in rows if row["error"]]
            result["recoverable"] = any(row["finished"] or self._orphaned(row) for row in rows)
            result["running_tasks"] = [{"session": row["session"], "run": row["run"]}
                                       for row in rows if not row["finished"] and not self._orphaned(row)]
            result["running_runs"] = len(result["running_tasks"])
            result["cleanup_runs"] = len(rows) - result["running_runs"]
            with _recovery_lock:
                result["recovering"] = str(self.path.resolve()) in _recovery_jobs
            return result

    def request_recovery(self):
        """Retry owned, finished sessions off the run/UI thread, at most 3 times.

        Polling snapshot never starts a new batch. Only a task boundary, a switch
        or an explicit retry can request another batch after this one finishes.
        """
        key = str(self.path.resolve())
        with _recovery_lock:
            if key in _recovery_jobs:
                return _recovery_jobs[key]
            def work():
                try:
                    for attempt, delay in enumerate((0, 1, 3), 1):
                        if delay:
                            threading.Event().wait(delay)
                        log.info("browser.recovery_started attempt=%s", attempt)
                        state = self.recover(prepare=True)
                        if not state["recoverable"]:
                            log.info("browser.recovery_completed attempt=%s", attempt)
                            return
                    log.warning("browser.recovery_exhausted cleanup_runs=%s", state["cleanup_runs"])
                except Exception:
                    log.exception("browser.recovery_failed")
                finally:
                    with _recovery_lock:
                        _recovery_jobs.pop(key, None)
            thread = threading.Thread(target=work, daemon=True, name="browser-recovery")
            _recovery_jobs[key] = thread
            thread.start()
            return thread

    @staticmethod
    def _orphaned(row):
        return (row["pid"] == os.getpid() and row["process_token"] != _PROCESS_TOKEN) or not _alive(row["pid"])

    def save(self, enabled):
        if self.config is None:
            raise BrowserBackendError("unavailable", "浏览器设置存储不可用。")
        # Serialize preference writes against lease acquisition in both processes.
        with self._transaction() as db:
            previous = self._desired() == INTERNAL
            self.config._save_browser_backend_preference({"enabled": enabled is True})
            try:
                result = self._state(db)
                db.commit()
            except Exception:
                # Restore preference before releasing the coordinator lock.
                try:
                    self.config._save_browser_backend_preference({"enabled": previous})
                except Exception as restore_error:
                    raise BrowserBackendError("browser_save_uncertain", "浏览器设置写入后未能确认切换，也未能恢复原设置。请检查配置目录后重新读取当前状态。") from restore_error
                raise
        log.info("browser.backend_preference_saved desired=%s pending=%s", result["desired"], result["pending"])
        return result

    def acquire(self, session, run, snapshot, backend):
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM finished_runs WHERE session=? AND run=?", (session, run)).fetchone():
                raise BrowserBackendError("browser_run_finished", "本轮浏览器任务已结束，旧调用不会再次执行。")
            state = self._state(db)
            if snapshot.get("backend") != backend or state["backend"] != backend or snapshot.get("generation") != state["generation"]:
                raise BrowserBackendError("browser_backend_changed", "浏览器后端已切换或本轮未启用此后端。请在新一轮任务继续；本次操作未执行。")
            lease = db.execute("SELECT * FROM leases WHERE session=? AND run=?", (session, run)).fetchone()
            if lease and lease["finished"]:
                raise BrowserBackendError("browser_run_finished", "本轮浏览器任务已结束，不能重新执行旧操作。")
            if not lease and state["pending"]:
                raise BrowserBackendError("browser_switch_pending", "正在等待已有浏览器任务结束后切换，暂不启动新的浏览器任务。")
            if not lease:
                db.execute("INSERT INTO leases(session,run,backend,pid,process_token) VALUES (?,?,?,?,?)",
                           (session, run, backend, os.getpid(), _PROCESS_TOKEN))
                log.info("browser.run_acquired session=%s run=%s backend=%s", session, run, backend)
            db.execute("UPDATE leases SET calls=calls+1 WHERE session=? AND run=?", (session, run))

    def complete_call(self, session, run):
        with self._transaction() as db:
            db.execute("UPDATE leases SET calls=MAX(0,calls-1) WHERE session=? AND run=?", (session, run))
            row = db.execute("SELECT * FROM leases WHERE session=? AND run=?", (session, run)).fetchone()
        if row and row["finished"] and not row["calls"]:
            self.finish(session, run, background=True)

    def track_session(self, session, run, external_id, *, stopped=False):
        with self._transaction() as db:
            row = db.execute("SELECT sessions FROM leases WHERE session=? AND run=?", (session, run)).fetchone()
            if not row:
                raise BrowserBackendError("browser_run_finished", "浏览器任务已经结束。")
            ids = set(json.loads(row[0]))
            ids.discard(external_id) if stopped else ids.add(external_id)
            db.execute("UPDATE leases SET sessions=? WHERE session=? AND run=?", (json.dumps(sorted(ids)), session, run))

    def owns_session(self, session, run, external_id):
        with self._transaction() as db:
            row = db.execute("SELECT sessions FROM leases WHERE session=? AND run=?", (session, run)).fetchone()
            return bool(row and external_id in json.loads(row[0]))

    def finish(self, session, run, *, background=False):
        with self._transaction() as db:
            db.execute("INSERT OR IGNORE INTO finished_runs VALUES (?,?)", (session, run))
            db.execute("UPDATE leases SET finished=1 WHERE session=? AND run=?", (session, run))
            row = db.execute("SELECT * FROM leases WHERE session=? AND run=?", (session, run)).fetchone()
            if not row:
                return
            if not row["calls"] and not json.loads(row["sessions"]):
                db.execute("DELETE FROM leases WHERE session=? AND run=?", (session, run))
                self._state(db)
                log.info("browser.run_released session=%s run=%s", session, run)
                return
            if row["calls"]:
                return  # complete_call schedules cleanup once the call settles.
        if background:
            self.request_recovery()
        else:
            self._cleanup(session, run)

    def _cleanup(self, session, run, *, deadline=None):
        # Keep the row until cleanup is confirmed, so a switch cannot race it.
        from .file_write_locks import file_write_lock
        with file_write_lock(str(self.path) + ".cleanup"):
            self._cleanup_locked(session, run, deadline=deadline)

    def _cleanup_locked(self, session, run, *, deadline=None):
        with self._transaction() as db:
            row = db.execute("SELECT * FROM leases WHERE session=? AND run=?", (session, run)).fetchone()
            if not row or row["calls"] or not row["finished"]:
                return
            ids = json.loads(row["sessions"])
        errors = []
        for external_id in ids:
            try:
                remaining = 10 if deadline is None else min(10, int(deadline - time.monotonic()))
                if remaining < 1:
                    raise TimeoutError("本次浏览器恢复已超时，已有内容已保留。")
                from .browser_skill_component import _stop_session_after_interruption, _run_bsk, _parse_json_output
                if external_id.startswith("request:"):
                    cancellation = _run_bsk(["--json", "--quiet", "session", "request", external_id[len("request:"):], "--cancel"], timeout=remaining)
                    payload = _parse_json_output(cancellation["stdout"], cancellation["stderr"])
                    result = {"stopped": cancellation["returncode"] == 0
                              and isinstance(payload, dict) and payload.get("state") == "closed"
                              and payload.get("request_id") == external_id[len("request:"):],
                              "error": cancellation["stderr"] or cancellation["stdout"]}
                else:
                    result = (_stop_session_after_interruption(external_id) if deadline is None else
                              _stop_session_after_interruption(external_id, timeout_seconds=remaining))
                if not result.get("stopped"):
                    raise RuntimeError(result.get("error") or "浏览器操作尚未确认结束。")
                self.track_session(session, run, external_id, stopped=True)
            except Exception as exc:
                errors.append(str(exc))
        with self._transaction() as db:
            if errors:
                db.execute("UPDATE leases SET error=? WHERE session=? AND run=?", ("；".join(errors), session, run))
                log.warning("browser.cleanup_failed session=%s run=%s error=%s", session, run, "；".join(errors))
            else:
                db.execute("DELETE FROM leases WHERE session=? AND run=? AND finished=1 AND calls=0", (session, run))
                self._state(db)
                log.info("browser.run_released session=%s run=%s", session, run)

    def recover(self, *, prepare=False):
        with self._transaction() as db:
            rows = db.execute("SELECT * FROM leases").fetchall()
            keys = []
            for row in rows:
                orphaned = self._orphaned(row)
                if orphaned or row["finished"] and not row["calls"]:
                    db.execute("INSERT OR IGNORE INTO finished_runs VALUES (?,?)", (row["session"], row["run"]))
                    db.execute("UPDATE leases SET finished=1, calls=0 WHERE session=? AND run=?", (row["session"], row["run"]))
                    keys.append((row["session"], row["run"]))
        if prepare and keys and any(json.loads(row["sessions"]) for row in rows if (row["session"], row["run"]) in keys):
            from .browser_skill_component import prepare_browser_recovery
            prepare_browser_recovery()
        deadline = time.monotonic() + 20
        for key in keys:
            self._cleanup(*key, deadline=deadline)
        return self.snapshot()

    def run_active(self, session, run):
        with self._transaction() as db:
            row = db.execute("SELECT 1 FROM leases WHERE session=? AND run=?", (session, run)).fetchone()
            return bool(row)


def bind_run(config, authorization, *, snapshot=None, worker=False):
    with _binding_lock:
        state = authorization._browser_state
        if not state:
            coordinator = BrowserBackendCoordinator(config)
            pinned = snapshot or coordinator.snapshot()
            state.update(coordinator=coordinator, snapshot={k: pinned[k] for k in ("backend", "generation")}, workers=0, finished=False)
        if worker:
            state["workers"] += 1
        return dict(state["snapshot"])


def finish_run(authorization, *, background=False):
    with _binding_lock:
        state = authorization._browser_state
        if not state or state.get("finished"):
            return
        state["workers"] = max(0, state["workers"] - 1)
        if state["workers"]:
            return
        state["finished"] = True
    state["coordinator"].finish(authorization.session_id, authorization.run_id, background=background)


@contextmanager
def browser_operation(context, backend):
    from .execution_authorization import current_authorization, ExecutionAuthorization
    auth = current_authorization(context)
    if type(auth) is not ExecutionAuthorization or not auth._browser_state:
        raise BrowserBackendError("browser_context_unavailable", "缺少可信的浏览器运行上下文，本次操作未执行。")
    auth.check()
    state = auth._browser_state
    if state.get("finished"):
        raise BrowserBackendError("browser_run_finished", "本轮浏览器任务已结束，本次操作未执行。")
    coordinator = state["coordinator"]
    coordinator.acquire(auth.session_id, auth.run_id, state["snapshot"], backend)
    try:
        yield coordinator, auth
    finally:
        coordinator.complete_call(auth.session_id, auth.run_id)


def run_backend(run_context=None, config=None):
    value = (run_context or {}).get("browser_backend")
    if isinstance(value, dict) and value.get("backend") in {INTERNAL, EXTERNAL}:
        return value["backend"]
    if config is None:
        return INTERNAL
    if not isinstance(getattr(config, "config_path", None), (str, Path)):
        getter = getattr(config, "get", None)
        return INTERNAL if callable(getter) and normalize_policy(getter(CONFIG_KEY, {}))["enabled"] else EXTERNAL
    return BrowserBackendCoordinator(config).snapshot()["backend"]


def tool_allowed(name, backend):
    return (name != "browser_skill_cli" or backend == EXTERNAL) and (name not in WEB_TOOLS or backend == INTERNAL)


def project_definition(definition, backend):
    if backend == INTERNAL:
        return definition
    function = definition.get("function") or {}
    name = function.get("name")
    if name not in SHARED_TOOLS:
        return definition
    result = json.loads(json.dumps(definition))
    function = result["function"]
    descriptions = {"browser_tabs": "List this conversation's file and terminal tabs, including unsaved/conflict state. Web tabs are unavailable in external browser mode.",
                    "browser_open": "Open a file or persistent interactive terminal in this conversation without stealing user focus.",
                    "browser_close": "Close a file or terminal tab; refuses unsaved user changes. Web tabs are unavailable in external browser mode."}
    function["description"] = descriptions[name]
    if name == "browser_open":
        function["parameters"]["properties"]["kind"]["enum"] = ["file", "terminal"]
        function["parameters"]["properties"].pop("url", None)
    return result
