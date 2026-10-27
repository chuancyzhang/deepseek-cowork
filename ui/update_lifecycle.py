"""Update-specific quiesce/save/exit handshake, separate from ordinary exit."""
import json
import os
import sys
import time

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication

from core.app_version import APP_VERSION
from core.agent_manager import get_agent_manager_registry
from core.runtime_journal import RUN_NONTERMINAL_STATUSES
from core.theme import default_design_tokens
from core.theme_service import resolve_theme
from core.update_coordinator import atomic_json, update_log
from core.updater import create_windows_update_script, launch_windows_update_script
from ui.primitives import ProductMessageDialog
from ui.worker_lifecycle import pending_ui_workers, pause_ui_work_timers, ui_worker_snapshot

INSTALLER_STARTUP_WAIT_SECONDS = 180


class UpdateCall(QThread):
    def __init__(self, call, parent):
        super().__init__(parent)
        self.call, self.result, self.error = call, None, None

    def run(self):
        try:
            self.result = self.call()
        except Exception as exc:
            self.error = str(exc)


class UpdateLifecycleMixin:
    def _update_call(self, call, continuation):
        worker = UpdateCall(call, self)
        self._update_call_worker = worker
        def finish():
            self._update_call_worker = None
            worker.deleteLater()
            if worker.error:
                self._abort_update(worker.error)
            else:
                try:
                    continuation(worker.result)
                except Exception as exc:
                    self._abort_update(str(exc))
        worker.finished.connect(finish)
        worker.start()

    def begin_update_install(self, result):
        if getattr(self, "_update_preparing", False) or getattr(self, "_checkpoint_shutdown_pending", False):
            return
        if any(getattr(state, "branch_in_progress", False) for state in self.sessions.values()) or any(w.isRunning() for w in getattr(self, "skillhub_workers", [])):
            self.update_coordinator.install_aborted("文件操作尚未完成，请稍后再更新；安装包已保留。")
            return
        if not self._confirm_leave_product_page() or not self.confirm_leave_deliverable_edit("更新应用"):
            self.update_coordinator.install_aborted()
            return
        self._update_result = result
        self._update_transaction = ""
        self._update_preparing = True
        self._update_paused_timers = []
        self._update_daemon_prepared = False
        self._update_gateway_stopped = False
        # Query the connected daemon even when there are no visible running sessions.
        client = getattr(self, "daemon_client", None)
        self._update_call(lambda: client.status() if client else None, self._update_check_tasks)

    def _update_check_tasks(self, status):
        daemon_expected = bool(getattr(self, "daemon_available", False) or any(getattr(s, "daemon_running", False) for s in self.sessions.values()))
        if daemon_expected and (not isinstance(status, dict) or status.get("status") != "ok"):
            self._abort_update("无法确认后台任务状态，请稍后重试；安装包已保留。")
            return
        self._update_daemon_status = status if isinstance(status, dict) and status.get("status") == "ok" else None
        active = any(getattr(s, "daemon_running", False) or (getattr(s, "llm_worker", None) and s.llm_worker.isRunning()) for s in self.sessions.values())
        active = active or bool((status or {}).get("active_runs")) or bool(get_agent_manager_registry().active_count(self.chat_storage.db_path))
        if active:
            choice = ProductMessageDialog("安装更新", "更新会停止正在运行的任务，已完成内容会保留。", "confirm",
                [("停止任务并更新", "install", "primary", True), ("暂不更新", "cancel", "secondary", False)], parent=self).exec_result("cancel")
            if choice != "install":
                self._abort_update("更新已准备好，尚未安装", notify=False)
                return
        try:
            theme = default_design_tokens()
            if self.theme_manager:
                repository = self.theme_manager.repository
                resolved = resolve_theme(repository.get_theme(repository.load().active_theme_id), default_design_tokens())
                theme = resolved["tokens"]
            extra = []
            if self._update_daemon_status:
                extra.append(self._update_daemon_status["pid"])
            for name in ("daemon_process", "gateway_process"):
                process = getattr(self, name, None)
                if process and process.poll() is None:
                    extra.append(process.pid)
            script = create_windows_update_script(self.update_coordinator.install_dir, self._update_result["staged_app_dir"],
                self._update_result["change_plan_path"], current_pid=os.getpid(), exe_name=os.path.basename(sys.executable),
                target_dir=self.update_coordinator.root, extra_wait_pids=extra,
                theme={"bg": theme["bg_main"], "text": theme["text_primary"], "primary": theme["primary"],
                       "font": resolved.get("font_family", "Microsoft YaHei UI") if self.theme_manager else "Microsoft YaHei UI",
                       "font_size": theme["font_size_body"], "spacing": theme["spacing_lg"]},
                version=self._update_result["release"]["tag_name"])
            self._update_transaction = os.path.dirname(script)
            self._update_installer_process = launch_windows_update_script(script)
            self._update_deadline = time.monotonic() + INSTALLER_STARTUP_WAIT_SECONDS
            self.update_coordinator.publish(
                can_cancel_install=True,
                message="正在等待安装程序启动；若安全软件弹出确认，请允许本次安装。应用和任务暂时保持运行。",
            )
            update_log(self.update_coordinator.root, "installer_wait_start", transaction=os.path.basename(self._update_transaction), wait_seconds=INSTALLER_STARTUP_WAIT_SECONDS)
            self._schedule_update_installer_check()
        except Exception as exc:
            self._abort_update(f"无法启动安装程序：{exc}")

    def _schedule_update_installer_check(self):
        transaction = self._update_transaction
        QTimer.singleShot(250, lambda: self._await_update_installer(transaction))

    def _cancel_update_startup(self):
        if self._update_preparing and self.update_coordinator.state.get("can_cancel_install"):
            self._abort_update("已取消本次安装，应用和任务保持运行；安装包已保留，可再次安装。")

    def _await_update_installer(self, transaction=None):
        if not self._update_preparing or (transaction is not None and transaction != self._update_transaction):
            return
        root = self._update_transaction
        if os.path.isfile(os.path.join(root, "abort.json")) or self._update_installer_process.poll() is not None:
            self._abort_update("安装程序已关闭，应用尚未退出。")
        elif os.path.isfile(os.path.join(root, "ready.json")):
            try:
                with open(os.path.join(root, "ready.json"), encoding="utf-8-sig") as handle:
                    ready = json.load(handle)
            except (ValueError, OSError) as exc:
                self._abort_update(f"无法读取安装程序确认信息：{exc}")
                return
            if ready.get("id") != os.path.basename(root):
                self._abort_update("安装程序确认信息不匹配。")
                return
            update_log(self.update_coordinator.root, "installer_ready", transaction=os.path.basename(root))
            self._quiesce_for_update()
        elif time.monotonic() > self._update_deadline:
            update_log(self.update_coordinator.root, "installer_wait_slow", transaction=os.path.basename(root))
            choice = ProductMessageDialog(
                "安装程序尚未就绪",
                "应用尚未退出，安装包已保留。若 360 等安全软件正在询问，请允许本次安装；允许后可以继续等待。若已拒绝或没有响应，可以取消后重新安装。",
                "confirm",
                [("继续等待 3 分钟", "wait", "primary", True), ("取消本次安装", "cancel", "secondary", False)],
                parent=self,
            ).exec_result("cancel")
            if not self._update_preparing or self._update_transaction != root:
                return
            if choice == "wait":
                self._update_deadline = time.monotonic() + INSTALLER_STARTUP_WAIT_SECONDS
                self._schedule_update_installer_check()
            else:
                self._abort_update("已取消等待安装程序，应用尚未退出；安装包已保留。允许安全软件放行后可再次安装。")
        else:
            self._schedule_update_installer_check()

    def _quiesce_for_update(self):
        try:
            self._update_quiescing = True
            self._shutdown_wait_workers = ui_worker_snapshot(self)
            self.update_coordinator.publish(message="正在保存内容并停止任务…", can_cancel_install=False)
            self.sync_current_session_state()
            self.setEnabled(False)
            if self.runtime_checkpoint_worker is not None:
                self.runtime_checkpoint_worker.retry_failed()
            self._update_paused_timers.extend(pause_ui_work_timers(self))
            for state in list(self.sessions.values()):
                self._flush_ui_stream_buffers(state)
                worker = getattr(state, "llm_worker", None)
                if worker and worker.isRunning():
                    worker.stop()
                self._stop_live_subagents(state, force=False)
            self._update_deadline = time.monotonic() + 30
            def prepare():
                get_agent_manager_registry().stop_for_update(self.chat_storage.db_path)
                if self._update_daemon_status:
                    self._update_daemon_prepared = True
                    # Do not send an update request to a daemon that changed identity.
                    current = self.daemon_client.ping()
                    if not current or current.get("pid") != self._update_daemon_status.get("pid") or current.get("signature") != self._update_daemon_status.get("signature"):
                        raise RuntimeError("后台进程已变化，请重新尝试更新。")
                    response = self.daemon_client._request({"action": "prepare_update"}, timeout=35)
                    if not response or not response.get("saved"):
                        raise RuntimeError((response or {}).get("error") or "后台内容未确认保存。")
                return True
            self._update_call(prepare, self._update_daemon_saved)
        except Exception as exc:
            self._abort_update(str(exc))

    def _update_daemon_saved(self, _result):
        self._update_daemon_prepared = True
        self._update_checkpoint_queued = False
        self._await_update_saves()

    def _await_update_saves(self):
        if not self._update_preparing:
            return
        try:
            if os.path.isfile(os.path.join(self._update_transaction, "abort.json")) or self._update_installer_process.poll() is not None:
                raise RuntimeError("安装程序已关闭；内容已保留，尚未安装。")
            running = pending_ui_workers(self)
            if running:
                if time.monotonic() > self._update_deadline:
                    raise RuntimeError("任务仍在停止和保存，尚未关闭应用，请稍后重试。")
                QTimer.singleShot(100, self._await_update_saves)
                return
            if get_agent_manager_registry().active_count(self.chat_storage.db_path):
                raise RuntimeError("子任务尚未停止和保存，请稍后重试。")
            if not self._update_checkpoint_queued:
                for state in list(self.sessions.values()):
                    self._flush_ui_stream_buffers(state)
                    self.flush_session_content(state.session_id, final=False)
                    self.flush_session_thinking(state.session_id)
                    run_id = getattr(state, "active_turn_request_id", "")
                    run = self.runtime_journal.get_run(state.session_id, run_id) if run_id else None
                    if run and run.get("status") in RUN_NONTERMINAL_STATUSES:
                        if not self._queue_runtime_checkpoint(state, boundary="exit_local"):
                            raise RuntimeError("运行恢复记录未进入保存队列，尚未关闭应用。")
                    if not self.save_chat_history(state.session_id):
                        raise RuntimeError("会话保存失败，尚未关闭应用。")
                self._update_checkpoint_queued = True
                QTimer.singleShot(100, self._await_update_saves)
                return
            checkpoint = getattr(self, "runtime_checkpoint_worker", None)
            if checkpoint:
                drained, failed = checkpoint.drain_status()
                if failed:
                    raise RuntimeError("运行恢复记录保存失败，尚未关闭应用。")
                if not drained or self._checkpoint_continuations:
                    if time.monotonic() > self._update_deadline:
                        raise RuntimeError("运行恢复记录仍在保存，请稍后重试。")
                    QTimer.singleShot(100, self._await_update_saves)
                    return
            if not self.flush_pending_chat_saves(timeout_ms=3000):
                raise RuntimeError("消息尚未保存完成，尚未关闭应用。")
            # The normal save queue includes composer drafts and skips pristine
            # views. Its acknowledgement is the sole persistence boundary here;
            # a second unconditional upsert would create empty history records.
            self._commit_update_exit()
        except Exception as exc:
            self._abort_update(str(exc))

    def _commit_update_exit(self):
        # Nothing may kill the parent before all persistence acknowledgements above.
        if pending_ui_workers(self):
            raise RuntimeError("后台操作尚未完成，尚未开始安装。")
        root = self._update_transaction
        update_log(self.update_coordinator.root, "saved_for_update", transaction=os.path.basename(root), update_id=getattr(self, "_update_result", {}).get("update_id"))
        if self._update_daemon_status:
            response = self.daemon_client.shutdown()
            if not response or response.get("status") != "ok":
                raise RuntimeError("后台进程未接受退出请求，尚未开始安装。")
        self._update_gateway_stopped = bool(getattr(self, "gateway_process", None))
        self.stop_gateway_process()
        if os.path.isfile(os.path.join(root, "abort.json")) or self._update_installer_process.poll() is not None:
            raise RuntimeError("安装程序已关闭，应用保持打开；尚未替换文件。")
        atomic_json(os.path.join(root, "commit.json"), {"id": os.path.basename(root), "saved": True})
        self._update_exit_committed = True
        # Writers stay usable through all abortable steps. After commit, keep
        # the event loop alive until their actual native threads have returned.
        for worker in (getattr(self, "runtime_checkpoint_worker", None), getattr(self, "chat_save_worker", None)):
            if worker is not None:
                worker.request_stop()
        QTimer.singleShot(0, self._finish_update_exit)

    def _finish_update_exit(self):
        if not getattr(self, "_update_exit_committed", False):
            return
        if pending_ui_workers(self, include_writers=True):
            QTimer.singleShot(100, self._finish_update_exit)
            return
        try:
            self._dispose_application_event_filters()
            self.skill_catalog_service.stop_watching()
            if self.tray_icon:
                self.tray_icon.hide()
        except Exception as exc:
            update_log(self.update_coordinator.root, "exit_cleanup_error", error=str(exc))
        finally:
            # Once committed, no exception is allowed to revive an application
            # whose files the independent installer is permitted to replace.
            self._checkpoint_close_ready = True
            QApplication.quit()

    def _abort_update(self, message, notify=True):
        root = getattr(self, "_update_transaction", "")
        if root:
            try:
                atomic_json(os.path.join(root, "abort.json"), {"id": os.path.basename(root)})
            except OSError:
                process = getattr(self, "_update_installer_process", None)
                if process and process.poll() is None:
                    process.terminate()
        self._update_preparing = False
        if getattr(self, "_update_quiescing", False):
            self.setEnabled(True)
            self._update_quiescing = False
        for timer in getattr(self, "_update_paused_timers", []):
            timer.start()
        self._update_paused_timers = []
        client = getattr(self, "daemon_client", None)
        if client and getattr(self, "_update_daemon_prepared", False):
            worker = UpdateCall(lambda: client._request({"action": "cancel_update"}), self)
            worker.finished.connect(worker.deleteLater)
            self._update_cancel_worker = worker
            worker.start()
        if getattr(self, "_update_gateway_stopped", False):
            self.start_gateway_process()
        self.update_coordinator.install_aborted(message)
        update_log(self.update_coordinator.root, "install_aborted", error=message)
        if notify:
            self.add_system_toast(message, "error", auto_close_ms=8000)

    def acknowledge_update_startup(self):
        receipt = os.environ.pop("COWORK_UPDATE_RECEIPT", "")
        identity = os.environ.pop("COWORK_UPDATE_ID", "")
        if receipt and identity:
            expected = os.path.join(self.update_coordinator.root, "transactions", identity, "opened.json")
            if os.path.normcase(os.path.abspath(receipt)) == os.path.normcase(os.path.abspath(expected)) and len(identity) == 32 and all(c in "0123456789abcdef" for c in identity):
                atomic_json(receipt, {"id": identity, "version": APP_VERSION})
