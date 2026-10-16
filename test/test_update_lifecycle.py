"""Exercise save/exit barriers without launching or quitting the user's application."""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, QThread, QTimer
from PySide6.QtWidgets import QApplication
from core.chat_storage import ChatStorage
from core.runtime_journal import RuntimeJournal
from core.update_coordinator import UpdateCoordinator
from ui.update_lifecycle import UpdateLifecycleMixin


class Host(QObject, UpdateLifecycleMixin):
    pass


class UpdateLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.host = Host()
        host = self.host
        host.update_coordinator = UpdateCoordinator(str(self.root), True, root=str(self.root), parent=host)
        host.chat_storage = ChatStorage(str(self.root / "chat.db"))
        host.runtime_journal = RuntimeJournal(str(self.root / "runtime"))
        host.sessions = {}
        host._update_preparing = True
        host._update_transaction = str(self.root / "transaction")
        Path(host._update_transaction).mkdir()
        host._update_installer_process = SimpleNamespace(poll=lambda: None)
        host._update_checkpoint_queued = False
        host._update_deadline = time.monotonic() + 30
        host._update_paused_timers = []
        host._update_daemon_prepared = False
        host._update_daemon_status = None
        host._checkpoint_continuations = {}
        host.theme_manager = None
        host.chat_save_worker = MagicMock()
        host.runtime_checkpoint_worker = MagicMock()
        host.runtime_checkpoint_worker.drain_status.return_value = (True, False)
        host.add_system_toast = MagicMock()
        host._flush_ui_stream_buffers = MagicMock()
        host.flush_session_content = MagicMock()
        host.flush_session_thinking = MagicMock()
        host._queue_runtime_checkpoint = MagicMock(return_value=True)
        host.save_chat_history = MagicMock(return_value=True)
        host.flush_pending_chat_saves = MagicMock(return_value=True)
        host._compose_session_meta = lambda state: {"test": state.session_id}
        host.stop_gateway_process = MagicMock()
        host.setEnabled = MagicMock()

    def tearDown(self):
        self.host.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def state(self, identity, run_id=""):
        state = SimpleNamespace(session_id=identity, active_turn_request_id=run_id,
                                composer_draft="未发送的草稿", prompt_files=["attachment.txt"])
        self.host.sessions[identity] = state
        return state

    def test_delayed_ready_is_accepted_after_original_thirty_second_limit(self):
        host = self.host
        host._update_deadline = time.monotonic() + 120
        host._quiesce_for_update = MagicMock()
        with patch("ui.update_lifecycle.QTimer.singleShot"):
            host._await_update_installer()
        host._quiesce_for_update.assert_not_called()
        Path(host._update_transaction, "ready.json").write_text(json.dumps({"id": "transaction"}))
        host._await_update_installer()
        host._quiesce_for_update.assert_called_once()

    def test_expired_wait_can_be_extended_without_aborting_or_stopping_tasks(self):
        host = self.host
        host._update_deadline = 0
        with patch("ui.update_lifecycle.ProductMessageDialog") as dialog, patch("ui.update_lifecycle.QTimer.singleShot"):
            dialog.return_value.exec_result.return_value = "wait"
            host._await_update_installer()
        self.assertTrue(host._update_preparing)
        self.assertFalse(Path(host._update_transaction, "abort.json").exists())
        self.assertGreater(host._update_deadline, time.monotonic() + 170)
        host.chat_save_worker.stop_worker.assert_not_called()

    def test_cancel_wait_and_old_timer_cannot_resume_a_new_transaction(self):
        host = self.host
        host.update_coordinator.publish(phase="installing", can_cancel_install=True)
        host.update_coordinator.installation_cancel_requested.connect(host._cancel_update_startup)
        old = host._update_transaction
        host.update_coordinator.cancel()
        self.assertTrue(Path(old, "abort.json").exists())
        self.assertFalse(host._update_preparing)
        host._update_preparing = True
        host._update_transaction = str(self.root / "second")
        host._quiesce_for_update = MagicMock()
        host._await_update_installer(old)
        host._quiesce_for_update.assert_not_called()


    def test_busy_confirmation_cancel_does_not_start_installer_or_stop_tasks(self):
        host = self.host
        self.state("background")
        with patch("ui.update_lifecycle.ProductMessageDialog") as dialog, patch("ui.update_lifecycle.create_windows_update_script") as launch:
            dialog.return_value.exec_result.return_value = "cancel"
            host._update_check_tasks({"status": "ok", "active_runs": 1})
        dialog.assert_called_once()
        launch.assert_not_called()
        self.assertFalse(host._update_preparing)
        host.chat_save_worker.stop_worker.assert_not_called()

    def test_unsaved_edit_cancel_keeps_prepared_package(self):
        host = self.host
        host._update_preparing = False
        host._confirm_leave_product_page = lambda: True
        host.confirm_leave_deliverable_edit = lambda _: False
        host._update_call = MagicMock()
        host.begin_update_install({"staged_app_dir": "prepared"})
        host._update_call.assert_not_called()
        self.assertFalse(host._update_preparing)

    def test_save_failure_aborts_without_commit_and_keeps_writers(self):
        host = self.host
        self.state("s")
        host.save_chat_history.return_value = False
        with patch("ui.update_lifecycle.QTimer.singleShot"):
            host._await_update_saves()
        self.assertFalse(Path(host._update_transaction, "commit.json").exists())
        self.assertTrue(Path(host._update_transaction, "abort.json").exists())
        self.assertFalse(host._update_preparing)
        host.chat_save_worker.stop_worker.assert_not_called()
        self.assertEqual(host.sessions["s"].composer_draft, "未发送的草稿")

    def test_all_drafts_saved_after_queue_drains_and_completed_run_not_interrupted(self):
        host = self.host
        self.state("finished", "r")
        self.state("empty")
        host.runtime_journal.begin_run("finished", "r", turn_id="t", writer_owner="ui:1", base_messages=[])
        host.runtime_journal.update_run("finished", "r", {"status": "completed"})
        host._commit_update_exit = MagicMock()
        with patch("ui.update_lifecycle.QTimer.singleShot"):
            host._await_update_saves()
            host._await_update_saves()
        host._queue_runtime_checkpoint.assert_not_called()
        host._commit_update_exit.assert_called_once()
        for identity in ("finished", "empty"):
            meta = host.chat_storage.get_conversation_record(identity)["meta"]
            self.assertEqual(meta["update_composer"]["text"], "未发送的草稿")
            self.assertEqual(meta["update_composer"]["files"], ["attachment.txt"])

    def test_failed_commit_file_does_not_stop_writers_or_quit(self):
        host = self.host
        with patch("ui.update_lifecycle.atomic_json", side_effect=OSError("disk full")), patch("ui.update_lifecycle.QApplication.quit") as quit_app:
            with self.assertRaises(OSError):
                host._commit_update_exit()
        host.chat_save_worker.stop_worker.assert_not_called()
        host.chat_save_worker.request_stop.assert_not_called()
        host.runtime_checkpoint_worker.request_stop.assert_not_called()
        quit_app.assert_not_called()

    def test_commit_waits_for_native_writer_exit_before_disposing_ui(self):
        class SlowWriter(QThread):
            def __init__(self, parent):
                super().__init__(parent)
                self.release = threading.Event()
                self.stopping = False

            def request_stop(self):
                self.stopping = True

            def run(self):
                self.release.wait(10)

        host = self.host
        host.chat_save_worker = SlowWriter(host)
        host.chat_save_worker.start()
        host._dispose_application_event_filters = MagicMock()
        host.skill_catalog_service = MagicMock()
        host.tray_icon = None
        with patch("ui.update_lifecycle.QApplication.quit") as quit_app:
            try:
                host._commit_update_exit()
                self.assertTrue(Path(host._update_transaction, "commit.json").exists())
                self.assertTrue(host.chat_save_worker.stopping)
                ticks = []
                QTimer.singleShot(0, lambda: ticks.append(True))
                self.app.processEvents()
                self.assertTrue(ticks)
                quit_app.assert_not_called()
                host._dispose_application_event_filters.assert_not_called()
                host.chat_save_worker.release.set()
                deadline = time.monotonic() + 3
                while not quit_app.called and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.005)
                quit_app.assert_called_once()
                host._dispose_application_event_filters.assert_called_once()
            finally:
                host.chat_save_worker.release.set()
                self.assertTrue(host.chat_save_worker.wait(3000))

    def test_abort_quiesce_restores_operations_and_timers(self):
        host = self.host
        host._update_quiescing = True
        timer = QTimer(host)
        timer.setInterval(1000)
        host._update_paused_timers = [timer]
        host._abort_update("save failed")
        self.assertFalse(host._update_quiescing)
        host.setEnabled.assert_called_once_with(True)
        self.assertTrue(timer.isActive())
        host.chat_save_worker.request_stop.assert_not_called()

    def test_runtime_save_failure_prevents_exit(self):
        host = self.host
        host._update_checkpoint_queued = True
        host.runtime_checkpoint_worker.drain_status.return_value = (True, True)
        host._commit_update_exit = MagicMock()
        host._await_update_saves()
        host._commit_update_exit.assert_not_called()
        self.assertFalse(host._update_preparing)

    def test_daemon_preparation_covers_unopened_and_detached_sessions(self):
        from core.daemon import DaemonState
        state = SimpleNamespace(lock=threading.RLock(), update_errors={}, update_requests={},
                                active_workers={"unopened": object()}, detached_workers={1: {"session_id": "detached"}},
                                chat_storage=self.host.chat_storage, stop_session=MagicMock(return_value={"released": True}))
        with patch("core.daemon.get_agent_manager_registry") as registry:
            result = DaemonState.prepare_update_shutdown(state)
        self.assertTrue(result["saved"])
        self.assertEqual({call.args[0] for call in state.stop_session.call_args_list}, {"unopened", "detached"})
        registry.return_value.stop_for_update.assert_called_once()

    def test_daemon_save_failure_never_reports_saved(self):
        from core.daemon import DaemonState
        state = SimpleNamespace(lock=threading.RLock(), update_errors={}, update_requests={},
                                active_workers={"unopened": object()}, detached_workers={}, chat_storage=self.host.chat_storage)
        def stop(*_, **__):
            state.update_errors["r"] = "disk full"
            return {"released": True}
        state.stop_session = stop
        with patch("core.daemon.get_agent_manager_registry"):
            result = DaemonState.prepare_update_shutdown(state)
        self.assertFalse(result["saved"])
        self.assertFalse(state.update_preparing)
        self.assertIn("disk full", result["error"])

    def test_log_export_includes_installer_diagnostics_but_not_backup_contents(self):
        from ui.log_export import LogExportWorker
        (self.root / "main.log").write_text("application")
        updates = self.root / "updates"
        transaction = updates / "transactions" / "test"
        (transaction / "backup").mkdir(parents=True)
        (updates / "app_update.log").write_text("download")
        (transaction / "update.log").write_text("installation")
        (transaction / "backup" / "secret.log").write_text("must not export")
        target = self.root / "export.txt"
        worker = LogExportWorker(str(self.root), str(target), MagicMock(), self.host)
        worker.run()
        self.assertFalse(worker.error)
        text = target.read_text(encoding="utf-8")
        self.assertIn("download", text)
        self.assertIn("installation", text)
        self.assertNotIn("must not export", text)
