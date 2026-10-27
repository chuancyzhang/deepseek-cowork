import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from core.chat_recovery_journal import ChatRecoveryJournal
from core.chat_save_queue import ChatSaveRequest
from core.chat_storage import ChatStorage
from main import MainWindow
from ui.worker_lifecycle import pending_ui_workers


class RecoveryHost(QObject):
    recover_runtime_pending_commits = MainWindow.recover_runtime_pending_commits
    _finish_runtime_recovery_scan = MainWindow._finish_runtime_recovery_scan
    _apply_runtime_recovery_scan = MainWindow._apply_runtime_recovery_scan


class RuntimeRecoveryScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.host = RecoveryHost()
        self.host.chat_recovery_journal = ChatRecoveryJournal(self.temp.name)
        self.host.chat_storage = ChatStorage(os.path.join(self.temp.name, "history.sqlite"))
        self.host.sessions = {}
        self.host.append_log = Mock()
        self.host.refresh_history_list = Mock()
        self.host.chat_recovery_journal.record(ChatSaveRequest(
            "s", [{"id": "u", "role": "user", "content": "必须恢复"}], "恢复", "draft", {}, 0, 1,
        ))

    def wait_until(self, condition):
        deadline = time.monotonic() + 4
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition())

    def tearDown(self):
        worker = getattr(self.host, "runtime_recovery_scan_worker", None)
        if worker:
            worker.wait(4000)
            self.wait_until(lambda: self.host.runtime_recovery_scan_worker is None)
        self.host.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_scan_keeps_event_loop_responsive_is_single_flight_and_rechecks_busy_state(self):
        entered = threading.Event()
        release = threading.Event()
        threads = []
        scan = self.host.chat_recovery_journal.scan_pending

        def slow_scan():
            threads.append(threading.get_ident())
            entered.set()
            release.wait(2)
            return scan()

        try:
            with patch.object(self.host.chat_recovery_journal, "scan_pending", side_effect=slow_scan) as read:
                self.host.recover_runtime_pending_commits()
                self.assertTrue(entered.wait(1))
                worker = self.host.runtime_recovery_scan_worker
                self.host.recover_runtime_pending_commits()
                self.assertIs(self.host.runtime_recovery_scan_worker, worker)
                # Task starts while discovery is in flight; its current state wins.
                self.host.sessions["s"] = SimpleNamespace(live_activity=True)
                QTimer.singleShot(0, release.set)
                self.wait_until(lambda: self.host.runtime_recovery_scan_worker is None)
                self.assertEqual(read.call_count, 1)
                self.assertNotEqual(threads, [threading.get_ident()])
                self.assertIsNone(self.host.chat_storage.get_conversation_record("s"))
            self.host.sessions.clear()
            self.host.recover_runtime_pending_commits()
            self.wait_until(lambda: self.host.runtime_recovery_scan_worker is None)
            self.assertEqual(len(self.host.chat_storage.get_messages("s")), 1)
        finally:
            release.set()

    def test_shutdown_waits_for_queued_callback_and_discards_read_only_result(self):
        self.host.recover_runtime_pending_commits()
        worker = self.host.runtime_recovery_scan_worker
        self.assertTrue(worker.wait(2000))
        self.assertIn(worker, pending_ui_workers(self.host))
        self.host._checkpoint_shutdown_pending = True
        self.wait_until(lambda: self.host.runtime_recovery_scan_worker is None)
        self.assertIsNone(self.host.chat_storage.get_conversation_record("s"))
        self.assertTrue(os.path.exists(self.host.chat_recovery_journal._path_for_session("s")))

    def test_scan_failure_is_reported_and_next_poll_can_retry(self):
        with patch.object(self.host.chat_recovery_journal, "scan_pending", side_effect=OSError("read failed")):
            self.host.recover_runtime_pending_commits()
            self.wait_until(lambda: self.host.runtime_recovery_scan_worker is None)
        self.host.append_log.assert_called_once()
        self.assertTrue(os.path.exists(self.host.chat_recovery_journal._path_for_session("s")))
        self.host.recover_runtime_pending_commits()
        self.wait_until(lambda: self.host.runtime_recovery_scan_worker is None)
        self.assertEqual(len(self.host.chat_storage.get_messages("s")), 1)
