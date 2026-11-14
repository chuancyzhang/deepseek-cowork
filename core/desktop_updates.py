"""One application-owned update lifecycle for community, enterprise and local ZIPs."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import threading
import time

from core import updater
from core.app_version import APP_VERSION, compare_versions
from core.update_coordinator import UpdateCoordinator, atomic_json, release_notes_text

log = logging.getLogger("desktop.updates")


def validate_release(value):
    from core.update_manifest import validate_manifest
    validate_manifest(value, layout="electron")
    return value


class DesktopUpdates:
    methods = frozenset({"updates.get", "updates.check", "updates.local", "updates.cancel", "updates.dismiss",
                         "updates.prepare_install", "updates.abort_install", "updates.commit_install", "updates.acknowledge"})

    def __init__(self, service):
        self.s = service
        self.root = Path(service.config.data_dir) / "updates" / "desktop"
        self.root.mkdir(parents=True, exist_ok=True)
        self.install_dir = Path(os.environ.get("COWORK_DESKTOP_INSTALL_DIR") or Path(__file__).resolve().parents[1]).resolve()
        self.packaged = updater.is_windows() and updater._is_valid_app_dir(self.install_dir, layout="electron")
        self.coordinator = UpdateCoordinator(str(self.install_dir), self.packaged, root=str(self.root), layout="electron")
        self.lock = threading.RLock()
        self.transaction = None
        self.plan = None
        self.plan_ready = threading.Event()
        self.dismissed = set()
        self.revision = time.time_ns()
        self.timer = None
        self.closed = False
        self.coordinator.changed.subscribe(self._changed)
        self.coordinator.installation_ready.subscribe(self._plan_ready)
        self.startup_request = {"id": os.environ.pop("COWORK_UPDATE_ID", ""), "receipt": os.environ.pop("COWORK_UPDATE_RECEIPT", ""), "install_dir": str(self.install_dir)}

    def _changed(self, _state):
        self.revision = max(self.revision + 1, time.time_ns())
        self.s.publish({"event": "update_state", "data": self.snapshot()})

    def _plan_ready(self, plan):
        self.plan = plan
        self.plan_ready.set()

    def snapshot(self):
        state = self.coordinator.snapshot()
        result = self.coordinator.prepared or state.get("result") or {}
        version = result.get("release", {}).get("tag_name", "")
        ready = bool(self.coordinator.prepared) and state["phase"] not in {"planning", "installing"}
        identity = version + ":" + str(result.get("sha256", ""))
        return {"revision": str(self.revision), "phase": state["phase"], "message": state["message"],
                "error": state.get("error", ""), "percent": state.get("percent", -1), "busy": state["busy"],
                "current_version": APP_VERSION, "version": version, "ready": ready,
                "can_install": ready and self.packaged and not state["install_requested"],
                "notice": ready and self.packaged and version not in self.dismissed,
                "identity": identity, "source": result.get("source_name") or state.get("source_name") or "",
                "reinstall": bool(version) and compare_versions(version, APP_VERSION) == 0,
                "notes": release_notes_text(result.get("release", {}).get("body", "")),
                "packaged": self.packaged, "local_manifest_verified": result.get("manifest_verified", False)}

    def page(self):
        from core.desktop_settings import action
        state = self.snapshot()
        actions = [action("update.check", "检查更新"), action("update.local", "选择本地安装包", open_kind="update")]
        if state["can_install"]:
            actions.append(action("update.install", "重启并重新安装" if state["reinstall"] else "重启并更新"))
        return {"title": "更新与关于", "values": {}, "revision": "", "sections": [{"title": "DeepSeek Cowork",
                "status": "当前版本 " + APP_VERSION, "actions": actions}]}

    def action(self, identity, p):
        if identity == "update.install":
            if not self.snapshot()["can_install"]:
                raise ValueError("请先在完整安装包中准备更新。")
            self.s.publish({"event": "update_install_requested"})
            return {"requested": True}
        return self.dispatch({"update.check": "updates.check", "update.local": "updates.local",
                              "update.cancel": "updates.cancel"}.get(identity, ""), p)

    def start_automatic(self):
        with self.lock:
            if self.timer or self.closed or not self.packaged:
                return
            self._schedule(15, initial=True)

    def _schedule(self, delay, initial=False):
        def tick():
            try:
                if not self.closed and not self.s.closing and not self.s.state.update_preparing:
                    if initial:
                        self.coordinator.start_automatic()
                    else:
                        self.coordinator.check()
            except Exception as exc:
                log.exception("automatic_update_failed")
                self.coordinator.publish(phase="error", message="暂时无法检查更新，可在设置中重试。", error=str(exc))
            finally:
                with self.lock:
                    if not self.closed:
                        self._schedule(24 * 60 * 60)
        self.timer = threading.Timer(delay, tick)
        self.timer.daemon = True
        self.timer.start()

    def close(self):
        with self.lock:
            self.closed = True
            if self.timer:
                self.timer.cancel()
        self.coordinator.cancel()
        worker = self.coordinator.worker
        if worker and not worker.join(35):
            raise RuntimeError("更新操作尚未结束，请稍后再退出。")

    def resume_automatic(self):
        """A failed application shutdown must not permanently disable checks."""
        with self.lock:
            if self.closed:
                self.closed = False
                self.timer = None
                if self.packaged:
                    self._schedule(24 * 60 * 60)

    def _unfreeze(self):
        with self.s.state.lock:
            self.s.state.update_preparing = False

    def _prepare_install(self, p):
        if not self.snapshot()["can_install"] or self.transaction:
            raise ValueError("没有可安装的更新，或另一次安装正在准备。")
        with self.s.state.lock:
            if self.s.store.active_runs() or self.s.jobs.active or self.s.state.update_requests:
                raise ValueError("请等后台任务和操作结束后再更新；已有内容已保留。")
            self.s.state.update_preparing = True
        self.plan = None
        self.plan_ready.clear()
        try:
            self.coordinator.request_install()
            while not self.plan_ready.wait(.1):
                snapshot = self.coordinator.snapshot()
                if not snapshot["busy"]:
                    raise ValueError(snapshot.get("error") or "更新准备已取消，当前版本继续可用。")
            plan = self.plan
            script = updater.create_windows_update_script(str(self.install_dir), plan["staged_app_dir"], plan["change_plan_path"],
                current_pid=int(p["host_pid"]), exe_name=updater.desktop_executable(plan["staged_app_dir"]),
                target_dir=str(self.root), extra_wait_pids=[os.getpid()], version=plan["release"]["tag_name"], layout="electron")
            self.transaction = Path(script).parent
            process = updater.launch_windows_update_script(script)
            deadline = time.monotonic() + 15
            while not (self.transaction / "ready.json").exists():
                if process.poll() is not None:
                    raise ValueError("安装器启动失败，当前版本继续运行；请查看更新诊断日志。")
                if time.monotonic() >= deadline:
                    raise ValueError("安装器准备超时，当前版本继续运行。")
                time.sleep(.1)
            log.info("installer_ready transaction=%s version=%s", self.transaction.name, plan["release"]["tag_name"])
            return {"id": self.transaction.name}
        except Exception as exc:
            if self.transaction:
                atomic_json(str(self.transaction / "abort.json"), {"id": self.transaction.name})
            self.transaction = None
            self._unfreeze()
            self.coordinator.install_aborted(str(exc))
            log.exception("install_prepare_failed")
            raise

    def dispatch(self, method, p):
        if method == "updates.get":
            return self.snapshot()
        if method == "updates.acknowledge":
            from core.update_startup import acknowledge_update_request
            acknowledged = bool(self.startup_request["id"] and acknowledge_update_request(
                self.startup_request, str(self.root), str(self.install_dir), APP_VERSION))
            if acknowledged:
                self.coordinator._select_channel()
                prepared = Path(self.coordinator.cache_root) / "prepared.json"
                if prepared.exists():
                    import json
                    value = json.loads(prepared.read_text(encoding="utf-8"))
                    if value.get("release", {}).get("tag_name") == APP_VERSION:
                        prepared.unlink()
            self.start_automatic()
            return {"acknowledged": acknowledged}
        with self.lock:
            if method in {"updates.check", "updates.local", "updates.cancel", "updates.dismiss"}:
                if self.transaction or self.coordinator.snapshot()["phase"] in {"planning", "installing"}:
                    raise ValueError("正在准备安装，请稍候。")
                if method == "updates.check":
                    self.coordinator.check()
                elif method == "updates.local":
                    if not self.packaged:
                        raise ValueError("开发运行请使用完整安装包进行本地更新。")
                    self.coordinator.select_local(str(p.get("path") or ""))
                elif method == "updates.cancel":
                    self.coordinator.cancel()
                else:
                    current = self.snapshot()
                    if current["identity"] == p.get("identity"):
                        self.dismissed.add(current["version"])
                        self._changed({})
                return self.snapshot()
            if method == "updates.prepare_install":
                return self._prepare_install(p)
            if not self.transaction or p.get("id") != self.transaction.name:
                raise ValueError("更新事务不存在。")
            if method == "updates.abort_install":
                atomic_json(str(self.transaction / "abort.json"), {"id": self.transaction.name})
                self.transaction = None
                self._unfreeze()
                self.coordinator.install_aborted()
                return {"aborted": True}
            if method == "updates.commit_install":
                self.s.dispatch("app.shutdown", {})
                atomic_json(str(self.transaction / "commit.json"), {"id": self.transaction.name})
                log.info("installer_committed transaction=%s", self.transaction.name)
                return {"committed": True}
        raise ValueError("不支持的更新操作。")
