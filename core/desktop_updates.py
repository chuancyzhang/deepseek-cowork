"""One application-owned update lifecycle for community, enterprise and local ZIPs."""
from __future__ import annotations

import logging
import json
import os
from pathlib import Path
import re
import threading
import time

from core import updater
from core.app_version import APP_VERSION, compare_versions
from core.update_coordinator import UpdateCoordinator, atomic_json, release_notes_text

log = logging.getLogger("desktop.updates")
PREPARE_TIMEOUT = 600
OWNER_TIMEOUT = 30
INSTALLER_READY_TIMEOUT = 15


def validate_release(value):
    from core.update_manifest import validate_manifest
    validate_manifest(value, layout="electron")
    return value


class DesktopUpdates:
    methods = frozenset({"updates.get", "updates.check", "updates.local", "updates.cancel", "updates.dismiss",
                         "updates.prepare_install", "updates.install_status", "updates.abort_install", "updates.commit_install", "updates.acknowledge"})

    def __init__(self, service):
        self.s = service
        self.root = Path(service.config.data_dir) / "updates" / "desktop"
        self.root.mkdir(parents=True, exist_ok=True)
        self.install_dir = Path(os.environ.get("COWORK_DESKTOP_INSTALL_DIR") or Path(__file__).resolve().parents[1]).resolve()
        self.packaged = updater.is_windows() and updater._is_valid_app_dir(self.install_dir, layout="electron")
        self.coordinator = UpdateCoordinator(str(self.install_dir), self.packaged, root=str(self.root), layout="electron")
        self.lock = threading.RLock()
        self.transaction = None
        self.attempt = None
        self.install_thread = None
        self.plan = None
        self.plan_ready = threading.Event()
        self.dismissed = set()
        self.revision = time.time_ns()
        self.timer = None
        self.closed = False
        self.coordinator.changed.subscribe(self._changed)
        self.coordinator.installation_ready.subscribe(self._plan_ready)
        self.startup_request = {"id": os.environ.pop("COWORK_UPDATE_ID", ""), "receipt": os.environ.pop("COWORK_UPDATE_RECEIPT", ""), "install_dir": str(self.install_dir)}
        self.last_install_result = self._recover_transactions()

    def _recover_transactions(self):
        """A restart may observe a transaction, but never resubmits its installation."""
        latest = None
        for directory in (self.root / "transactions").glob("*"):
            if not re.fullmatch(r"[0-9a-f]{32}", directory.name):
                continue
            try:
                config = json.loads((directory / "config.json").read_text(encoding="utf-8-sig"))
                if Path(config["install_dir"]).resolve() != self.install_dir:
                    continue
                record = json.loads((directory / "transaction.json").read_text(encoding="utf-8-sig"))
                if record.get("id") != directory.name:
                    continue
                if record.get("phase") in {"preparing", "ready", "committing", "cancelling", "cleanup_failed"} and not (directory / "commit.json").exists():
                    atomic_json(directory / "abort.json", {"id": directory.name})
                    atomic_json(directory / "transaction.json", {"id": directory.name, "phase": "cancelled",
                                "error": "上次安装准备已中断，尚未替换文件，可重新准备更新。"})
                    log.info("install_recovered_cancelled transaction=%s", directory.name)
                result_path = directory / "result.json"
                if result_path.exists():
                    result = json.loads(result_path.read_text(encoding="utf-8-sig"))
                    if result.get("id") == directory.name and (latest is None or result_path.stat().st_mtime > latest[0]):
                        latest = (result_path.stat().st_mtime, result)
            except (OSError, ValueError, KeyError, TypeError):
                log.exception("install_recovery_read_failed transaction=%s", directory.name)
        return latest[1] if latest and latest[1].get("phase") not in {"complete", "cancelled"} else None

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
        active = self.attempt and self.attempt["phase"] not in {"failed", "cancelled", "committed"}
        ready = bool(self.coordinator.prepared) and not active and state["phase"] not in {"planning", "installing"}
        identity = version + ":" + str(result.get("sha256", ""))
        return {"revision": str(self.revision), "phase": state["phase"], "message": state["message"],
                "error": state.get("error", ""), "percent": state.get("percent", -1), "busy": state["busy"],
                "current_version": APP_VERSION, "version": version, "ready": ready,
                "can_install": ready and self.packaged and not state["install_requested"],
                "notice": ready and self.packaged and version not in self.dismissed,
                "identity": identity, "source": result.get("source_name") or state.get("source_name") or "",
                "reinstall": bool(version) and compare_versions(version, APP_VERSION) == 0,
                "notes": release_notes_text(result.get("release", {}).get("body", "")),
                "packaged": self.packaged, "local_manifest_verified": result.get("manifest_verified", False),
                "install": self._attempt_snapshot(), "last_install_result": self.last_install_result,
                "can_cancel_install": bool(active and self.attempt["phase"] in {"preparing", "ready"})}

    def _attempt_snapshot(self):
        if not self.attempt:
            return None
        return {key: self.attempt[key] for key in ("id", "phase", "error")}

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
        if self.attempt and self.attempt["phase"] not in {"committing", "committed", "failed", "cancelled"}:
            self._abort_install({"id": self.attempt["id"]})
            if self.install_thread:
                self.install_thread.join(10)
                if self.install_thread.is_alive():
                    raise RuntimeError("更新准备仍在取消，请稍后再退出。")
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

    def _transaction_path(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{32}", identity):
            raise ValueError("更新事务编号无效。")
        return self.root / "transactions" / identity

    def _install_status(self, p):
        directory = self._transaction_path(p.get("id"))
        with self.lock:
            if self.attempt and self.attempt["id"] == directory.name:
                self.attempt["last_seen"] = time.monotonic()
                return self._attempt_snapshot()
        if (directory / "commit.json").exists():
            return {"id": directory.name, "phase": "committed", "error": ""}
        if (directory / "abort.json").exists():
            return {"id": directory.name, "phase": "cancelled", "error": ""}
        if (directory / "transaction.json").exists():
            return json.loads((directory / "transaction.json").read_text(encoding="utf-8-sig"))
        return {"id": directory.name, "phase": "unknown", "error": "尚未收到安装准备请求。"}

    def _set_install_phase(self, phase, error=""):
        self.attempt.update(phase=phase, error=error)
        if phase != "committed":  # After commit, only the independent installer writes its journal.
            atomic_json(self.transaction / "transaction.json", self._attempt_snapshot())
        log.info("install_state transaction=%s phase=%s reason=%s", self.attempt["id"], phase, error)
        self._changed(None)

    def _prepare_install(self, p):
        directory = self._transaction_path(p.get("id"))
        if directory.exists():
            return self._install_status(p)  # Idempotent, including an abort arriving before prepare.
        if not self.snapshot()["can_install"] or self.install_thread and self.install_thread.is_alive():
            raise ValueError("没有可安装的更新，或另一次安装正在准备。")
        host_pid = int(p["host_pid"])
        if host_pid <= 0:
            raise ValueError("应用进程编号无效。")
        with self.s.state.lock:
            if self.s.store.active_runs() or self.s.jobs.active or self.s.state.update_requests:
                raise ValueError("请等后台任务和操作结束后再更新；已有内容已保留。")
            self.s.state.update_preparing = True
        self.transaction = directory
        self.attempt = {"id": directory.name, "phase": "preparing", "error": "", "cancel": threading.Event(),
                        "last_seen": time.monotonic(), "process": None}
        try:
            atomic_json(directory / "config.json", {"id": directory.name, "install_dir": str(self.install_dir)})
            self._set_install_phase("preparing")
            self.install_thread = threading.Thread(target=self._monitor_install, args=(host_pid,), name="desktop-install", daemon=True)
            self.install_thread.start()
            return self._attempt_snapshot()
        except Exception:
            self.attempt.update(phase="failed", error="无法保存安装准备状态，尚未开始安装。")
            self._unfreeze()
            raise

    def _monitor_install(self, host_pid):
        attempt = self.attempt
        started = time.monotonic()
        def check():
            if attempt["cancel"].is_set():
                raise updater.UpdateCancelled("已取消安装准备，当前版本继续可用。")
            if time.monotonic() - attempt["last_seen"] > OWNER_TIMEOUT:
                raise updater.UpdaterError("应用连接已中断，已撤销安装准备。")
            if time.monotonic() - started > PREPARE_TIMEOUT:
                raise updater.UpdaterError("安装准备超时，已撤销本次安装，请重试。")
        try:
            self.plan = None
            self.plan_ready.clear()
            check()
            self.coordinator.request_install()
            while not self.plan_ready.wait(.1):
                check()
                snapshot = self.coordinator.snapshot()
                if not snapshot["busy"]:
                    raise ValueError(snapshot.get("error") or "更新准备已取消，当前版本继续可用。")
            plan = self.plan
            check()
            script = updater.create_windows_update_script(str(self.install_dir), plan["staged_app_dir"], plan["change_plan_path"],
                current_pid=host_pid, exe_name=updater.desktop_executable(plan["staged_app_dir"]),
                target_dir=str(self.root), extra_wait_pids=[os.getpid()], version=plan["release"]["tag_name"], layout="electron",
                transaction_id=attempt["id"])
            check()
            process = updater.launch_windows_update_script(script)
            attempt["process"] = process
            deadline = time.monotonic() + INSTALLER_READY_TIMEOUT
            while not (self.transaction / "ready.json").exists():
                check()
                if process.poll() is not None:
                    from core.update_diagnostics import installer_exit_details, installer_startup_failure_message
                    raise ValueError(installer_startup_failure_message(installer_exit_details(self.transaction, process)))
                if time.monotonic() >= deadline:
                    raise ValueError("安装器准备超时，当前版本继续运行。")
                time.sleep(.1)
            with self.lock:
                check()
                self._set_install_phase("ready")
            while True:
                with self.lock:
                    if attempt["phase"] == "committed":
                        return
                    committing = attempt["phase"] == "committing"
                if not committing:
                    check()
                    if process.poll() is not None:
                        raise ValueError("安装器已退出，尚未开始安装，请重新准备。")
                time.sleep(.1)
        except Exception as exc:
            # Cancellation cannot race past commit. The monitor never changes installed files.
            with self.lock:
                if attempt["phase"] in {"committing", "committed"}:
                    log.exception("install_monitor_failed_after_commit")
                    return
                attempt["cancel"].set()
                attempt.update(phase="cancelling", error=str(exc))
                try:
                    atomic_json(self.transaction / "abort.json", {"id": attempt["id"]})
                    self._set_install_phase("cancelling", str(exc))
                except OSError:
                    # A full/unavailable disk must not skip stopping the owned, uncommitted helper.
                    log.exception("install_abort_record_failed transaction=%s", attempt["id"])
                    self._changed(None)
            self.coordinator.cancel()
            worker = self.coordinator.worker
            if worker:
                worker.join()  # The input gate remains closed until all late callbacks settle.
            process = attempt["process"]
            try:
                if process and process.poll() is None:
                    import subprocess
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        log.warning("installer_cancel_terminate transaction=%s pid=%s", attempt["id"], process.pid)
                        process.terminate()  # Owned process handle, before commit only.
                        process.wait(timeout=5)
                with self.lock:
                    self.coordinator.install_aborted(str(exc))
                    self._unfreeze()
                    self._set_install_phase("cancelled" if isinstance(exc, updater.UpdateCancelled) else "failed", str(exc))
            except Exception:
                log.exception("install_cancel_cleanup_failed transaction=%s", attempt["id"])
                with self.lock:
                    self._set_install_phase("cleanup_failed", "安装器尚未退出，请重试取消或查看诊断日志。")

    def _abort_install(self, p):
        directory = self._transaction_path(p.get("id"))
        with self.lock:
            if directory.joinpath("commit.json").exists() or (self.attempt and self.attempt["id"] == directory.name
                    and self.attempt["phase"] in {"committing", "committed"}):
                phase = "committed" if directory.joinpath("commit.json").exists() else self.attempt["phase"]
                return {"id": directory.name, "phase": phase, "error": "安装已进入提交阶段，由安装器完成或恢复。"}
            if self.attempt and self.attempt["id"] == directory.name and self.attempt["phase"] not in {"failed", "cancelled"}:
                retry_cleanup = self.attempt["phase"] == "cleanup_failed"
                self.attempt["cancel"].set()
                self._set_install_phase("cancelling", "正在取消安装准备…")
                if retry_cleanup:
                    self.install_thread = threading.Thread(target=self._monitor_install, args=(0,), name="desktop-install-cleanup", daemon=True)
                    self.install_thread.start()
                return self._attempt_snapshot()
            atomic_json(directory / "abort.json", {"id": directory.name})
            return {"id": directory.name, "phase": "cancelled", "error": ""}

    def _commit_install(self, p):
        with self.lock:
            status = self._install_status(p)
            if status["phase"] in {"committing", "committed"}:
                return status
            if status["phase"] != "ready" or self.attempt["cancel"].is_set():
                raise ValueError("安装尚未准备好或已经取消。")
            if self.attempt["process"].poll() is not None:
                raise ValueError("安装器已退出，请重新准备。")
            self._set_install_phase("committing")
        try:
            self.s.dispatch("app.shutdown", {})
            atomic_json(self.transaction / "commit.json", {"id": self.transaction.name})
            with self.lock:
                self._set_install_phase("committed")
                return self._attempt_snapshot()
        except Exception:
            with self.lock:
                self._set_install_phase("ready", "应用尚未完成退出，安装未提交。")
                self.attempt["cancel"].set()
            raise

    def dispatch(self, method, p):
        if method == "updates.get":
            return self.snapshot()
        if method == "updates.acknowledge":
            from core.update_startup import acknowledge_update_request
            request = p.get("request") or self.startup_request
            if p.get("host_version") and p["host_version"] != APP_VERSION:
                raise ValueError("窗口与后台版本不一致，无法确认更新完成，请退出后重新打开应用。")
            acknowledged = bool(request.get("id") and acknowledge_update_request(
                request, str(self.root), str(self.install_dir), APP_VERSION))
            if acknowledged:
                transaction = self._transaction_path(request["id"])
                recorded = json.loads((transaction / "transaction.json").read_text(encoding="utf-8-sig"))
                if recorded.get("phase") in {"installed", "installed_not_opened", "complete"}:
                    self.last_install_result = None
                self.coordinator._select_channel()
                prepared = Path(self.coordinator.cache_root) / "prepared.json"
                if prepared.exists():
                    value = json.loads(prepared.read_text(encoding="utf-8"))
                    if value.get("release", {}).get("tag_name") == APP_VERSION:
                        prepared.unlink()
                self._changed(None)
            self.start_automatic()
            return {"acknowledged": acknowledged}
        if method == "updates.install_status":
            return self._install_status(p)
        if method == "updates.abort_install":
            return self._abort_install(p)
        if method == "updates.commit_install":
            return self._commit_install(p)
        if method == "updates.cancel" and self.attempt and self.attempt["phase"] not in {"failed", "cancelled", "committed"}:
            self._abort_install({"id": self.attempt["id"]})
            return self.snapshot()
        with self.lock:
            if method in {"updates.check", "updates.local", "updates.cancel", "updates.dismiss"}:
                if (self.attempt and self.attempt["phase"] not in {"failed", "cancelled", "committed"}) or self.coordinator.snapshot()["phase"] in {"planning", "installing"}:
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
        raise ValueError("不支持的更新操作。")
