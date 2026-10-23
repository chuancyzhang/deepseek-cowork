"""Real Qt event ordering: queued completion, reentrancy, and safe GUI exit."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")

from PySide6.QtCore import QObject, QThread, QEvent, QTimer, QFileSystemWatcher
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

import main
from core.chat_save_queue import ChatSaveRequest, ChatSaveWorker
from core.chat_storage import ChatStorage
from core.runtime_checkpoint import CheckpointRequest, RuntimeCheckpointWorker
from core.runtime_journal import RuntimeJournal
from ui.worker_lifecycle import pending_ui_workers


class Host(QObject):
    queue_daemon_connection = main.MainWindow.queue_daemon_connection
    _drain_daemon_connection_queue = main.MainWindow._drain_daemon_connection_queue
    _finish_daemon_connection = main.MainWindow._finish_daemon_connection
    show_tray_status = main.MainWindow.show_tray_status
    _finish_daemon_status = main.MainWindow._finish_daemon_status
    handle_daemon_status_finished = main.MainWindow.handle_daemon_status_finished
    refresh_deliverables = main.MainWindow.refresh_deliverables
    _deliverable_scan_scope = main.MainWindow._deliverable_scan_scope
    _watch_deliverable_paths = main.MainWindow._watch_deliverable_paths
    handle_deliverable_fs_changed = main.MainWindow.handle_deliverable_fs_changed
    _start_deliverable_scan = main.MainWindow._start_deliverable_scan
    _finish_deliverable_scan = main.MainWindow._finish_deliverable_scan
    _handle_deliverable_scan_completed = main.MainWindow._handle_deliverable_scan_completed
    _shutdown_workers_async = main.MainWindow._shutdown_workers_async
    shutdown_workers = main.MainWindow.shutdown_workers
    _await_shutdown_workers = main.MainWindow._await_shutdown_workers
    _finish_shutdown_workers = main.MainWindow._finish_shutdown_workers
    _wait_for_shutdown = main.MainWindow._wait_for_shutdown
    _abort_worker_shutdown = main.MainWindow._abort_worker_shutdown
    _handle_runtime_checkpoint_completed = main.MainWindow._handle_runtime_checkpoint_completed

    def __init__(self, root):
        super().__init__()
        self.workspace_dir = str(root)
        self.daemon_host, self.daemon_port, self.daemon_runtime_signature = "127.0.0.1", 1, "test"
        self.daemon_client = object()
        self.daemon_process = self.daemon_connect_worker = self.daemon_status_worker = None
        self.tray_icon = None
        self.sessions = {}
        self.results, self.rendered, self.scan_errors = [], [], []
        self.on_result = self.on_render = None
        self.current_session_id = "scan-session"
        self.current_deliverable_path = ""
        self.deliverable_items = []
        self.deliverables_list = MagicMock()
        self.deliverable_scan_generation = 0
        self.deliverable_scan_view = None
        self.deliverable_scan_pending = None
        self.deliverable_scanner = None
        self.deliverable_scan_worker = None
        self.deliverable_refresh_timer = QTimer(self)
        self.deliverable_refresh_timer.setSingleShot(True)
        self.deliverable_refresh_timer.timeout.connect(self._start_deliverable_scan)
        self.chat_storage = ChatStorage(str(root / "chat.db"))
        self.runtime_journal = RuntimeJournal(str(root / "journal"))
        self.skill_catalog_service = MagicMock()
        self.skill_manager_ready = True
        self._checkpoint_continuations = {}
        self.runtime_checkpoint_worker = self.chat_save_worker = None
        self._dispose_application_event_filters = MagicMock()
        self.refresh_context_badges = MagicMock()
        self.add_system_toast = MagicMock()
        self.sync_current_session_state = MagicMock()
        self._flush_ui_stream_buffers = MagicMock()
        self.flush_session_content = MagicMock()
        self.flush_session_thinking = MagicMock()
        self.save_chat_history = MagicMock(return_value=True)
        self.setEnabled = MagicMock()
        self._reconnect_daemon_stream = MagicMock()
        self._queue_runtime_checkpoint = MagicMock(return_value=True)
        self.append_log = MagicMock()

    def get_session(self, identity):
        return self.sessions.get(identity)

    def handle_daemon_connect_finished(self, payload):
        self.results.append(payload)
        main.MainWindow.handle_daemon_connect_finished(self, payload)
        if self.on_result:
            callback, self.on_result = self.on_result, None
            callback()

    def _sync_file_browser_empty_state(self, **kwargs):
        self.file_state = kwargs

    def _apply_deliverable_items(self, items, render_current=False):
        self.deliverable_items = list(items)
        self.rendered.append((items, render_current))
        if self.on_render:
            callback, self.on_render = self.on_render, None
            callback()

    def _handle_deliverable_scan_failed(self, error, request):
        self.scan_errors.append((error, request["generation"]))
        main.MainWindow._handle_deliverable_scan_failed(self, error, request)


class HeldWorker(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.release = threading.Event()
        self.stop_requested = False

    def request_stop(self):
        self.stop_requested = True

    def run(self):
        self.release.wait(10)


class WorkerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.host = Host(self.root)
        self.held = []
        self.scan_gates = []
        self.log = patch("main.log_ui_navigation").start()
        self.qt_errors = []
        patch("sys.excepthook", side_effect=lambda kind, value, trace: self.qt_errors.append(value)).start()
        self.registry = patch("main.get_agent_manager_registry").start().return_value
        self.registry.active_count.return_value = 0

    def tearDown(self):
        self.host._checkpoint_shutdown_pending = True
        self.host.deliverable_refresh_timer.stop()
        for gate in self.scan_gates:
            gate.set()
        if self.host.deliverable_scanner is not None:
            self.assertTrue(self.host.deliverable_scanner.wait(3000))
        for worker in self.held:
            worker.release.set()
            worker.wait(2000)
        for worker in (self.host.runtime_checkpoint_worker, self.host.chat_save_worker):
            if worker is not None and isValid(worker):
                worker.request_stop()
                self.assertTrue(worker.wait(3000))
        self.app.processEvents()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        for worker in self.host.findChildren(QThread):
            self.assertTrue(worker.wait(3000), type(worker).__name__)
        self.app.processEvents()
        self.host.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)
        patch.stopall()
        self.temp.cleanup()
        self.assertEqual(self.qt_errors, [])

    def until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate(), {
            "scan_pending": self.host.deliverable_scan_pending,
            "scan_worker": self.host.deliverable_scan_worker,
            "scan_errors": self.host.scan_errors,
            "qt_errors": self.qt_errors,
            "recent_events": self.log.mock_calls[-8:],
        })

    def client(self):
        return SimpleNamespace(ping=lambda: {"signature": "test"}, status=lambda: {"status": "ok", "sessions": 1})

    def start_writers(self):
        host = self.host
        host.chat_save_worker = ChatSaveWorker(host.chat_storage.db_path, debounce_ms=0, parent=host)
        host.runtime_checkpoint_worker = RuntimeCheckpointWorker(host.runtime_journal, host)
        host.runtime_checkpoint_worker.completed.connect(host._handle_runtime_checkpoint_completed)
        host.chat_save_worker.start()
        host.runtime_checkpoint_worker.start()

    def test_finished_connection_stays_occupied_until_queued_callback(self):
        with patch("main.DaemonClient", side_effect=lambda *a, **k: self.client()) as factory:
            self.host.queue_daemon_connection(allow_start=True)
            old = self.host.daemon_connect_worker
            self.assertTrue(old.wait(1000))
            self.host.queue_daemon_connection(allow_start=True, retries=2)
            self.assertIs(self.host.daemon_connect_worker, old)
            self.assertEqual(factory.call_count, 1)
            self.until(lambda: len(self.host.results) == 2 and self.host.daemon_connect_worker is None)
            self.assertEqual(factory.call_count, 2)

    def test_reentrant_result_keeps_slot_until_result_processing_returns(self):
        with patch("main.DaemonClient", side_effect=lambda *a, **k: self.client()) as factory:
            def reenter():
                current = self.host.daemon_connect_worker
                self.host.queue_daemon_connection(allow_start=True)
                self.app.processEvents()
                self.assertIs(self.host.daemon_connect_worker, current)
                self.assertEqual(factory.call_count, 1)
            self.host.on_result = reenter
            self.host.queue_daemon_connection(allow_start=True)
            self.until(lambda: len(self.host.results) == 2 and self.host.daemon_connect_worker is None)

    def test_connection_error_releases_slot_and_retains_diagnostic(self):
        with patch("main.DaemonClient", side_effect=RuntimeError("probe failed")):
            self.host.queue_daemon_connection(allow_start=True)
            self.until(lambda: self.host.daemon_connect_worker is None)
        self.assertEqual(self.host.results[0]["error"], "probe failed")
        self.assertFalse(self.host.daemon_available)

    def test_result_handler_exception_releases_slot_and_runs_queued_request(self):
        def fail_after_enqueue():
            self.host.queue_daemon_connection(allow_start=True)
            raise ValueError("result handler failed")
        self.host.on_result = fail_after_enqueue
        with patch("main.DaemonClient", side_effect=lambda *a, **k: self.client()):
            self.host.queue_daemon_connection(allow_start=True)
            self.until(lambda: len(self.host.results) == 2 and self.host.daemon_connect_worker is None)
        self.assertEqual([str(error) for error in self.qt_errors], ["result handler failed"])
        self.qt_errors.clear()

    def test_close_accepts_completed_connection_result_without_starting_pending(self):
        with patch("main.DaemonClient", side_effect=lambda *a, **k: self.client()) as factory:
            self.host.queue_daemon_connection(allow_start=True)
            worker = self.host.daemon_connect_worker
            self.assertTrue(worker.wait(1000))
            process = object()
            worker.result["process"] = process
            self.host.queue_daemon_connection(allow_start=True)
            self.host._checkpoint_shutdown_pending = True
            self.until(lambda: self.host.daemon_connect_worker is None)
            self.assertIs(self.host.daemon_process, process)
            self.assertEqual(factory.call_count, 1)

    def test_status_dialog_reentry_does_not_start_another_query(self):
        self.host.daemon_available = True
        entered = []
        def information(_parent, _title, text):
            if text.startswith("守护进程:") and not entered:
                entered.append(True)
                worker = self.host.daemon_status_worker
                self.host.show_tray_status()
                self.app.processEvents()
                self.assertIs(self.host.daemon_status_worker, worker)
        with patch("main.DaemonClient", side_effect=lambda *a, **k: self.client()) as factory, patch("main.QMessageBox.information", side_effect=information):
            self.host.show_tray_status()
            self.until(lambda: self.host.daemon_status_worker is None)
            self.assertEqual(factory.call_count, 1)
        self.assertTrue(entered)

    def test_scan_refresh_after_native_finish_runs_latest_generation_once(self):
        actual = main.DeliverableScanWorker
        with patch("main.DeliverableScanWorker", side_effect=actual) as factory:
            self.host.refresh_deliverables()
            self.host.deliverable_refresh_timer.stop()
            self.host._start_deliverable_scan()
            old = self.host.deliverable_scan_worker
            self.assertTrue(old.wait(1000))
            self.host.refresh_deliverables(render_current=True)
            self.assertIs(self.host.deliverable_scan_worker, old)
            self.until(lambda: old.scan_count == 2 and self.host.deliverable_scan_worker is None)
        self.assertEqual(factory.call_count, 1)
        self.assertIs(self.host.deliverable_scanner, old)
        self.assertEqual(self.host.rendered, [([], False), ([], True)])

    def test_reentrant_scan_render_merges_refresh_without_overlapping_workers(self):
        def reenter():
            worker = self.host.deliverable_scan_worker
            self.host.refresh_deliverables(render_current=True)
            self.app.processEvents()
            self.assertIs(self.host.deliverable_scan_worker, worker)
        self.host.on_render = reenter
        self.host.refresh_deliverables()
        self.until(lambda: len(self.host.rendered) == 2 and self.host.deliverable_scan_worker is None)
        self.assertEqual(self.host.deliverable_scan_generation, 2)

    def test_scan_burst_coalesces_sources_and_does_not_postpone_active_timer(self):
        host = self.host
        for index in range(60):
            host.refresh_deliverables(reason="filesystem" if index % 2 else "generated_file")
        # Simulate an event arriving close to the end of the original coalescing window.
        host.deliverable_refresh_timer.start(30)
        host.refresh_deliverables(reason="tool_files_changed")
        self.assertEqual(host.deliverable_refresh_timer.interval(), 30)
        self.until(lambda: len(host.rendered) == 1)
        self.assertEqual(host.deliverable_scanner.scan_count, 1)
        self.assertEqual(host.deliverable_scanner.request["sources"],
                         ("filesystem", "generated_file", "tool_files_changed"))

    def test_scan_manual_request_is_prompt_and_uses_the_same_pending_slot(self):
        host = self.host
        host.refresh_deliverables(reason="filesystem")
        host.refresh_deliverables(render_current=True, immediate=True, reason="manual")
        self.assertEqual(host.deliverable_refresh_timer.interval(), 0)
        self.until(lambda: host.rendered)
        self.assertEqual(host.deliverable_scanner.scan_count, 1)
        self.assertEqual(host.rendered, [([], True)])

    def test_scan_reuses_one_object_and_one_completion_connection(self):
        host = self.host
        actual = main.DeliverableScanWorker
        with patch("main.DeliverableScanWorker", side_effect=actual) as factory:
            for index in range(200):
                host.refresh_deliverables(immediate=True, reason="manual")
                self.until(lambda: len(host.rendered) == index + 1)
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(host.deliverable_scanner.scan_count, 200)
        self.assertIsNone(host.deliverable_scan_worker)
        self.assertEqual(pending_ui_workers(host), [])

    def test_scan_reads_database_in_background_and_merges_requests_during_slow_read(self):
        host = self.host
        entered, release = threading.Event(), threading.Event()
        self.scan_gates.append(release)
        calls = []
        def read(workspace, prune_missing=True):
            calls.append(QThread.currentThread())
            if len(calls) == 1:
                entered.set()
                release.wait(3)
            return []
        with patch.object(host.chat_storage, "list_deliverables", side_effect=read):
            host.refresh_deliverables(immediate=True)
            self.until(entered.is_set)
            for _ in range(40):
                host.refresh_deliverables(reason="filesystem")
            ticked = []
            QTimer.singleShot(0, lambda: ticked.append(True))
            self.until(lambda: ticked)
            self.assertEqual(len(calls), 1)
            self.assertIsNot(calls[0], self.app.thread())
            release.set()
            self.until(lambda: len(host.rendered) == 2)
        self.assertEqual(len(calls), 2)
        self.assertIs(calls[0], calls[1])

    def test_scan_waits_for_native_cleanup_before_reusing_the_object(self):
        host = self.host
        host.refresh_deliverables(immediate=True)
        self.until(lambda: host.rendered)
        worker = host.deliverable_scanner
        self.assertTrue(worker.wait(1000))
        with patch.object(worker, "wait", new=lambda timeout: False):
            host.refresh_deliverables(immediate=True)
            self.app.processEvents()
            self.assertIsNone(host.deliverable_scan_worker)
            self.assertEqual(worker.scan_count, 1)
            self.assertIsNotNone(host.deliverable_scan_pending)
        self.until(lambda: len(host.rendered) == 2)
        self.assertEqual(worker.scan_count, 2)

    def test_scan_view_switch_discards_old_results_even_when_returning_to_same_view(self):
        host = self.host
        entered, release = threading.Event(), threading.Event()
        self.scan_gates.append(release)
        reads = []
        def read(workspace, prune_missing=True):
            reads.append(workspace)
            if len(reads) == 1:
                entered.set()
                release.wait(3)
            return []
        with patch.object(host.chat_storage, "list_deliverables", side_effect=read):
            host.refresh_deliverables(immediate=True)
            self.until(entered.is_set)
            host.current_session_id = "other-session"
            host.refresh_deliverables(immediate=True)
            host.current_session_id = "scan-session"
            host.refresh_deliverables(immediate=True)
            release.set()
            self.until(lambda: host.rendered)
        self.assertEqual(len(reads), 2)
        self.assertEqual(host.rendered, [([], False)])
        self.assertEqual(host.deliverable_scanner.request["view"][0], "scan-session")

    def test_scan_workspace_switch_uses_captured_database_scope_and_discards_old_files(self):
        host = self.host
        first, second = self.root / "first", self.root / "second"
        first.mkdir()
        second.mkdir()
        for workspace, name in ((first, "old.txt"), (second, "current.txt")):
            file = workspace / name
            file.write_text(name, encoding="utf-8")
            host.chat_storage.register_deliverable(str(workspace), str(file))
        host.workspace_dir = str(first)
        entered, release = threading.Event(), threading.Event()
        self.scan_gates.append(release)
        original = host.chat_storage.list_deliverables
        def read(workspace, **kwargs):
            if workspace == str(first):
                entered.set()
                release.wait(3)
            return original(workspace, **kwargs)
        with patch.object(host.chat_storage, "list_deliverables", side_effect=read):
            host.refresh_deliverables(immediate=True)
            self.until(entered.is_set)
            host.workspace_dir = str(second)
            host.refresh_deliverables(immediate=True, reason="workspace")
            release.set()
            self.until(lambda: host.rendered)
        self.assertEqual(len(host.rendered), 1)
        self.assertEqual([item["name"] for item in host.deliverable_items], ["current.txt"])
        self.assertEqual(host.deliverable_scanner.scan_count, 2)

    def test_scan_failure_keeps_results_and_explicit_retry_reuses_worker(self):
        host = self.host
        file = self.root / "report.md"
        file.write_text("# Ready", encoding="utf-8")
        host.chat_storage.register_deliverable(host.workspace_dir, str(file))
        host.refresh_deliverables(immediate=True)
        self.until(lambda: host.rendered)
        worker = host.deliverable_scanner
        kept = list(host.deliverable_items)
        with patch.object(host.chat_storage, "list_deliverables", side_effect=OSError("database unavailable")):
            host.refresh_deliverables(immediate=True)
            self.until(lambda: host.scan_errors)
        self.assertEqual(host.deliverable_items, kept)
        self.assertIn("已保留", host.file_state["error"])
        self.assertEqual(len(host.rendered), 1)
        host.refresh_deliverables(immediate=True, reason="retry")
        self.until(lambda: len(host.rendered) == 2)
        self.assertIs(host.deliverable_scanner, worker)
        self.assertEqual(worker.error, "")
        self.assertEqual(worker.scan_count, 3)

    def test_idle_reusable_scanner_does_not_block_shutdown(self):
        self.host.refresh_deliverables(immediate=True)
        self.until(lambda: self.host.rendered)
        scanner = self.host.deliverable_scanner
        self.start_writers()
        finished = []
        self.host._shutdown_workers_async(finished.append)
        self.until(lambda: finished)
        self.assertEqual(pending_ui_workers(self.host, include_writers=True), [])
        self.assertTrue(isValid(scanner))

    def test_shutdown_finishes_active_scan_without_launching_pending_refresh(self):
        host = self.host
        entered, release = threading.Event(), threading.Event()
        self.scan_gates.append(release)
        def read(*args, **kwargs):
            entered.set()
            release.wait(3)
            return []
        self.start_writers()
        with patch.object(host.chat_storage, "list_deliverables", side_effect=read):
            host.refresh_deliverables(immediate=True)
            self.until(entered.is_set)
            host.refresh_deliverables(reason="filesystem")
            finished = []
            host._shutdown_workers_async(finished.append)
            self.app.processEvents()
            self.assertFalse(finished)
            self.assertIn(host.deliverable_scanner, pending_ui_workers(host))
            release.set()
            self.until(lambda: finished)
        self.assertEqual(host.deliverable_scanner.scan_count, 1)
        self.assertIsNone(host.deliverable_scan_worker)

    def test_failed_shutdown_resumes_pending_scan_after_its_timer_already_fired(self):
        host = self.host
        entered, release = threading.Event(), threading.Event()
        self.scan_gates.append(release)
        reads = []
        def read(*args, **kwargs):
            reads.append(True)
            if len(reads) == 1:
                entered.set()
                release.wait(3)
            return []
        self.start_writers()
        host.sessions["s"] = SimpleNamespace(session_id="s", daemon_running=False,
            daemon_worker=None, llm_worker=None, active_turn_request_id="")
        host.save_chat_history.return_value = False
        with patch.object(host.chat_storage, "list_deliverables", side_effect=read):
            host.refresh_deliverables(immediate=True)
            self.until(entered.is_set)
            host.refresh_deliverables(immediate=True, reason="manual")
            self.until(lambda: not host.deliverable_refresh_timer.isActive())
            self.assertIsNotNone(host.deliverable_scan_pending)
            host._shutdown_workers_async(MagicMock())
            release.set()
            self.until(lambda: not host._checkpoint_shutdown_pending and len(host.rendered) == 2)
        self.assertEqual(len(reads), 2)
        self.assertIsNone(host.deliverable_scan_pending)

    def test_file_watcher_tracks_unselected_deliverables_without_reinstalling_watches(self):
        host = self.host
        workspace = self.root / "workspace"
        workspace.mkdir()
        host.workspace_dir = str(workspace)
        file = workspace / "unselected.md"
        file.write_text("first", encoding="utf-8")
        host.chat_storage.register_deliverable(str(workspace), str(file))
        host.deliverable_watcher = QFileSystemWatcher(host)
        host.deliverable_watcher.fileChanged.connect(host.handle_deliverable_fs_changed)
        host.deliverable_watcher.directoryChanged.connect(host.handle_deliverable_fs_changed)
        host.refresh_deliverables(immediate=True)
        self.until(lambda: host.rendered)
        host._watch_deliverable_paths()
        self.assertIn(str(file), [os.path.normpath(p) for p in host.deliverable_watcher.files()])
        with patch.object(host.deliverable_watcher, "removePaths", wraps=host.deliverable_watcher.removePaths) as remove:
            host._watch_deliverable_paths()
            remove.assert_not_called()
        file.write_text("updated externally", encoding="utf-8")
        self.until(lambda: host.deliverable_items[0]["size"] == len("updated externally"))
        file.unlink()
        self.until(lambda: not host.deliverable_items)

    def test_preview_resource_event_refreshes_preview_without_scanning_list(self):
        host = self.host
        resource = str(self.root / "chart.css")
        host._deliverable_preview_resources = {os.path.normcase(os.path.abspath(resource))}
        with patch("ui.file_refresh.schedule_file_refresh") as preview:
            host.handle_deliverable_fs_changed(resource)
            preview.assert_called_once_with(host)
        self.assertIsNone(host.deliverable_scan_pending)

    def test_shutdown_waits_for_slow_nested_worker_and_keeps_event_loop_alive(self):
        page = QObject(self.host)
        worker = HeldWorker(page)
        self.held.append(worker)
        worker.start()
        self.start_writers()
        finished = []
        self.host._shutdown_workers_async(finished.append)
        self.host._shutdown_workers_async(finished.append)
        ticks = []
        QTimer.singleShot(20, lambda: ticks.append(True))
        self.until(lambda: ticks)
        self.assertFalse(finished)
        self.host.setEnabled.assert_called_once_with(False)
        self.assertIn(worker, pending_ui_workers(self.host))
        self.host._dispose_application_event_filters.assert_not_called()
        self.assertTrue(self.host.chat_save_worker.isRunning())
        worker.release.set()
        self.until(lambda: finished)
        self.assertEqual(finished, [False])
        self.assertFalse(self.host.chat_save_worker.isRunning())
        self.assertFalse(self.host.runtime_checkpoint_worker.isRunning())
        self.host._dispose_application_event_filters.assert_called_once()

    def test_ordinary_exit_preserves_daemon_and_does_not_cancel_remote_run(self):
        host = self.host
        worker = HeldWorker()
        self.held.append(worker)
        worker.detach = MagicMock(side_effect=worker.release.set)
        worker.abort = MagicMock()
        worker._aborted = True
        worker.start()
        state = SimpleNamespace(session_id="s", daemon_running=True, daemon_worker=worker,
                                llm_worker=None, active_turn_request_id="r")
        host.sessions["s"] = state
        host.daemon_client = MagicMock()
        host.runtime_journal.begin_run("s", "r", turn_id="t", writer_owner="daemon", base_messages=[])
        self.start_writers()
        finished = []
        host._shutdown_workers_async(finished.append)
        with patch.object(main.MainWindow, "handle_daemon_response") as response:
            main.MainWindow._handle_native_run_worker_finished(host, "s", "t", "r", "daemon", worker)
            response.assert_not_called()
        self.until(lambda: finished)
        self.assertEqual(finished, [True])
        worker.detach.assert_called_once()
        worker.abort.assert_not_called()
        host.daemon_client.stop_session.assert_not_called()
        host._queue_runtime_checkpoint.assert_called_once_with(state, boundary="exit")
        self.assertTrue(state.daemon_running)

    def test_shutdown_processes_checkpoint_continuation_before_stopping_save_worker(self):
        host = self.host
        host.runtime_journal.begin_run("s", "r", turn_id="t", writer_owner="ui:1", base_messages=[])
        self.start_writers()
        request = CheckpointRequest("s", "r", "instance", 1, "last response", "")
        host._checkpoint_continuations[(*request.key, request.revision)] = lambda: host.chat_save_worker.enqueue(
            ChatSaveRequest("s", [{"role": "assistant", "content": "last response"}], "title", "completed", {}, 0, 1))
        host.runtime_checkpoint_worker.enqueue(request)
        finished = []
        host._shutdown_workers_async(finished.append)
        self.until(lambda: finished)
        self.assertEqual(host.chat_storage.get_messages("s")[0]["content"], "last response")
        self.assertFalse(host._checkpoint_continuations)

    def test_save_failure_leaves_application_and_writers_available(self):
        self.start_writers()
        self.host.sessions["s"] = SimpleNamespace(session_id="s", daemon_running=False,
            daemon_worker=None, llm_worker=None, active_turn_request_id="")
        self.host.save_chat_history.return_value = False
        finished = []
        self.host._shutdown_workers_async(finished.append)
        self.until(lambda: not self.host._checkpoint_shutdown_pending)
        self.assertFalse(finished)
        self.assertTrue(self.host.chat_save_worker.isRunning())
        self.assertTrue(self.host.runtime_checkpoint_worker.isRunning())
        self.host._dispose_application_event_filters.assert_not_called()
        self.assertEqual(self.host.setEnabled.call_args.args, (True,))

    def test_reference_draft_timer_pauses_for_final_save_and_resumes_after_failure(self):
        for success in (True, False):
            with self.subTest(success=success):
                host = Host(self.root)
                timer = QTimer(host)
                timer.setSingleShot(True)
                timer.start(600)
                host.sessions["s"] = SimpleNamespace(session_id="s", daemon_running=False,
                    daemon_worker=None, llm_worker=None, active_turn_request_id="", reference_draft_timer=timer)
                host.save_chat_history.return_value = success
                finished = []
                host._shutdown_workers_async(finished.append)
                self.assertFalse(timer.isActive())
                self.until(lambda: finished or not host._checkpoint_shutdown_pending)
                host.save_chat_history.assert_called_once_with("s")
                self.assertEqual(timer.isActive(), not success)
                timer.stop()
                host.deleteLater()

    def test_save_failure_reconnects_preserved_daemon_stream(self):
        self.start_writers()
        self.host.sessions["s"] = SimpleNamespace(session_id="s", daemon_running=True,
            daemon_worker=None, llm_worker=None, active_turn_request_id="r")
        self.host.save_chat_history.return_value = False
        self.host._shutdown_workers_async(MagicMock())
        self.until(lambda: not self.host._checkpoint_shutdown_pending)
        self.host._reconnect_daemon_stream.assert_called_once_with("s", "r")

    def test_stop_request_failure_restores_ui_without_disposing_workers(self):
        self.start_writers()
        self.registry.request_stop.side_effect = OSError("stop failed")
        finished = MagicMock()
        self.host._shutdown_workers_async(finished)
        self.assertFalse(self.host._checkpoint_shutdown_pending)
        self.assertTrue(self.host.chat_save_worker.isRunning())
        finished.assert_not_called()
        self.host._dispose_application_event_filters.assert_not_called()
        self.assertEqual(self.host.setEnabled.call_args.args, (True,))

    def test_failed_checkpoint_is_retried_only_on_explicit_exit(self):
        host = self.host
        host.runtime_journal.begin_run("s", "r", turn_id="t", writer_owner="ui:1", base_messages=[])
        self.start_writers()
        request = CheckpointRequest("s", "r", "instance", 1, "last response", "")
        with patch.object(host.runtime_journal, "write_checkpoint", side_effect=OSError("disk full")):
            host.runtime_checkpoint_worker.enqueue(request)
            self.until(lambda: host.runtime_checkpoint_worker.drain_status() == (True, True))
            self.app.processEvents()
            self.assertTrue(host.runtime_checkpoint_worker._failures)
        # Even if the run finished meanwhile, the retained recovery write must
        # no longer prevent the user's next explicit exit attempt forever.
        finished = []
        host._shutdown_workers_async(finished.append)
        self.until(lambda: finished)
        self.assertEqual(host.runtime_journal.get_run("s", "r")["draft_content"], "last response")
        self.assertEqual(host.runtime_checkpoint_worker.drain_status(), (True, False))

    def test_native_diagnostics_keep_previous_qt_handler_and_record_versions(self):
        from PySide6.QtCore import QtMsgType
        previous = MagicMock()
        with patch("main._NATIVE_CRASH_LOG_HANDLE", None), patch("main._QT_MESSAGE_HANDLER", None), \
             patch("main.get_app_data_dir", return_value=str(self.root)), patch("main.faulthandler.enable"), \
             patch("PySide6.QtCore.qInstallMessageHandler", return_value=previous), \
             patch("PySide6.QtCore.qFormatLogMessage", side_effect=lambda kind, context, message: message):
            main.install_native_crash_logging()
            try:
                main._QT_MESSAGE_HANDLER(QtMsgType.QtWarningMsg, None, "existing warning")
                main._QT_MESSAGE_HANDLER(QtMsgType.QtFatalMsg, None, "QThread: Destroyed while thread is still running")
            finally:
                main._NATIVE_CRASH_LOG_HANDLE.close()
        self.assertEqual(previous.call_count, 2)
        from core.runtime_logs import log_path
        text = log_path("native_crash", self.root).read_text(encoding="utf-8")
        self.assertIn("pyside=", text)
        self.assertIn("python=", text)
        self.assertIn("qt_fatal: QThread: Destroyed", text)


if __name__ == "__main__":
    unittest.main()
