"""Request ownership, terminal delivery and acknowledgement regression tests."""
import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from core.daemon import DaemonClient
from core.interaction import InteractionService, ask_user
import main
from main import DaemonStreamWorker, InlineInteractionCard, MainWindow


class InteractionLifecycleTests(unittest.TestCase):
    def service(self):
        service = InteractionService()
        ended = []
        service.interaction_finished.connect(ended.append, Qt.DirectConnection)
        return service, ended

    def test_all_kinds_preserve_answers_and_emit_one_end_without_answer_text(self):
        for kind, value, extra in (
            ("approval", True, {}), ("approval", False, {}), ("text", "private answer", {}),
            ("choice", "a", {"options": ["a", "b"]}),
            ("multi_choice", ["a", "b"], {"options": ["a", "b"]}),
            ("questionnaire", {"q": {"text": "private answer"}},
             {"questions": [{"id": "q", "question": "question"}]}),
        ):
            with self.subTest(kind=kind, value=value):
                service, ended = self.service()
                service.interaction_requested.connect(
                    lambda request: self.assertIs(service.resolve_request(request["request_id"], value), True),
                    Qt.DirectConnection,
                )
                result = service.create_request("session", kind, "question", timeout_seconds=86400,
                                                metadata={"run_id": "run"}, **extra)
                self.assertEqual(len(ended), 1)
                self.assertEqual(ended[0], {
                    "session_id": "session", "run_id": "run", "request_id": result["request_id"],
                    "status": result["status"], "approved": result["approved"], "answered": True,
                })
                self.assertNotIn("private answer", str(ended))
                self.assertIsNone(service.get_pending_request("session"))
                self.assertIs(service.resolve_request(result["request_id"], value), False)

    def test_explicit_deadline_never_fabricates_questionnaire_answers(self):
        for auto_select in (False, True):
            with self.subTest(auto_select=auto_select):
                service, ended = self.service()
                result = service.create_request("session", "questionnaire", "question", timeout_seconds=0.01,
                    questions=[{"id": "q", "question": "question", "options": ["recommended", "other"]}],
                    metadata={"auto_select_first_on_timeout": auto_select})
                self.assertEqual(result["status"], "timeout")
                self.assertFalse(result["approved"])
                self.assertEqual(ended[0]["status"], result["status"])
                self.assertFalse(ended[0]["answered"])
                self.assertEqual(result["answers"], {})

    def test_manual_questionnaire_survives_old_deadline_and_accepts_one_answer(self):
        service, ended = self.service()
        ready, release = threading.Event(), threading.Event()
        requests, results = [], []
        def capture(request):
            requests.append(request)
            ready.set()
            release.wait(3)
        service.interaction_requested.connect(capture, Qt.DirectConnection)
        thread = threading.Thread(target=lambda: results.append(service.create_request(
            "session", "questionnaire", "question", timeout_seconds=0.01, wait_for_submission=True,
            questions=[{"id": "q", "question": "question", "options": ["recommended", "other"]}])))
        thread.start()
        try:
            self.assertTrue(ready.wait(2))
            self.assertIsNone(requests[0]["timeout_seconds"])
            with patch("core.interaction.time.monotonic", return_value=10**12):
                self.assertIsNotNone(service.get_pending_request("session"))
                ack = service.respond_request(requests[0]["request_id"], {"q": {"text": "my answer"}})
                self.assertTrue(ack["resolved"])
                self.assertFalse(service.respond_request(requests[0]["request_id"], {"q": {"text": "duplicate"}})["resolved"])
        finally:
            release.set()
            service.cancel_session_requests("session")
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["answers"]["q"]["text"], "my answer")
        self.assertEqual([event["status"] for event in ended], ["completed"])

    def test_manual_wait_remains_cancellable_by_owner(self):
        service, ended = self.service()
        ready, abort = threading.Event(), threading.Event()
        results = []
        service.interaction_requested.connect(lambda _request: ready.set(), Qt.DirectConnection)
        thread = threading.Thread(target=lambda: results.append(service.create_request(
            "session", "questionnaire", "question", wait_for_submission=True, abort_check=abort.is_set)))
        thread.start()
        try:
            self.assertTrue(ready.wait(2))
        finally:
            abort.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["status"], "cancelled")
        self.assertEqual(len(ended), 1)

    def test_legacy_boolean_result_still_publishes_timeout(self):
        service, ended = self.service()
        with patch("core.interaction.interaction_service", service):
            result = ask_user("delete?", {"session_id": "session"}, timeout_seconds=0.01)
        self.assertIs(result, False)
        self.assertEqual([item["status"] for item in ended], ["timeout"])

    def test_answer_cancel_and_deadline_choose_one_outcome(self):
        # Keep the owner before its wait so both competitors run before it can
        # consume the outcome. No scheduler-dependent sleep or real operation.
        for first in ("answer", "cancel", "timeout", "invalid"):
            with self.subTest(first=first):
                service, ended = self.service()
                ready, release = threading.Event(), threading.Event()
                captured, results = [], []
                def capture(request):
                    captured.append(request)
                    ready.set()
                    release.wait(3)
                service.interaction_requested.connect(capture, Qt.DirectConnection)
                thread = threading.Thread(target=lambda: results.append(service.create_request(
                    "session", "approval", "question", timeout_seconds=180)))
                thread.start()
                try:
                    self.assertTrue(ready.wait(2))
                    request_id = captured[0]["request_id"]
                    self.assertEqual(captured[0]["timeout_seconds"], 180)
                    if first == "cancel":
                        self.assertEqual(service.cancel_session_requests("session"), 1)
                    elif first == "timeout":
                        with service._lock:
                            service._pending[request_id]["deadline"] = 0
                        self.assertIsNone(service.get_pending_request("session"))
                    elif first == "invalid":
                        invalid = service.respond_request(request_id, "not a yes/no reply")
                        self.assertEqual(invalid["reason"], "invalid")
                        self.assertIsNotNone(service.get_pending_request("session"))
                    acknowledgement = service.respond_request(request_id, True)
                    accepted = first in {"answer", "invalid"}
                    self.assertIs(acknowledgement["resolved"], accepted)
                    if not accepted:
                        self.assertEqual(acknowledgement["reason"], "ended")
                    self.assertEqual(service.cancel_session_requests("session"), 0)
                    self.assertIsNone(service.get_pending_request("session"))
                finally:
                    release.set()
                    thread.join(3)
                self.assertFalse(thread.is_alive())
                expected = {"answer": "completed", "cancel": "cancelled", "timeout": "timeout", "invalid": "completed"}[first]
                self.assertEqual(results[0]["status"], expected)
                self.assertEqual([item["status"] for item in ended], [expected])

    def test_end_notification_is_outside_lock_and_before_legacy_tool_return(self):
        service, ended = self.service()
        calls = []
        def finished(event):
            calls.append(service.get_pending_request(event["session_id"]))
            calls.append(service.resolve_request(event["request_id"], True))
        service.interaction_finished.connect(finished, Qt.DirectConnection)
        thread = threading.Thread(target=lambda: calls.append(service.create_request(
            "session", "approval", "question", timeout_seconds=0.01)), daemon=True)
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive(), "Terminal notification must not hold the interaction lock")
        self.assertEqual(calls[:2], [None, False])
        self.assertEqual(calls[2]["status"], "timeout")
        self.assertEqual(len(ended), 1)


class InteractionHost(QWidget):
    """Real interaction handlers/widgets without starting the whole application."""
    _show_inline_interaction_request = MainWindow._show_inline_interaction_request
    _place_inline_interaction_card = MainWindow._place_inline_interaction_card
    _restore_questionnaire_receipt = MainWindow._restore_questionnaire_receipt
    _finish_inline_interaction = MainWindow._finish_inline_interaction
    _finish_session_interactions = MainWindow._finish_session_interactions
    _dismiss_interaction_toast = MainWindow._dismiss_interaction_toast
    _event_matches_active_run = MainWindow._event_matches_active_run
    handle_interaction_request = MainWindow.handle_interaction_request
    handle_daemon_interaction_request = MainWindow.handle_daemon_interaction_request
    handle_interaction_finished = MainWindow.handle_interaction_finished
    refresh_session_activity_indicator = MainWindow.refresh_session_activity_indicator
    set_session_status = MainWindow.set_session_status

    def __init__(self):
        super().__init__()
        self.current_session_id = "session"
        self.state = SimpleNamespace(session_id="session", pending_interactions={}, messages=[],
            active_turn_id=1, active_turn_request_id="run", session_status="running", chat_layout=QVBoxLayout(self))
        self.state.chat_layout.addStretch()
        self.history_activity_indicators = {}
        self.history_activity_statuses = {"session": SimpleNamespace(setState=self._record_status)}
        self.history_age_labels = {}
        self.sidebar_status = "idle"
        for method in ("set_session_phase", "request_session_scroll_to_bottom", "add_system_toast",
                       "refresh_skill_capture_sidebar_indicator", "_queue_project_list_visibility_refresh",
                       "_cancel_token_speed_monitor", "_sync_history_age_for_session", "refresh_context_badges",
                       "_finalize_live_turn_process_groups", "_mark_session_favorite_run_completed"):
            setattr(self, method, Mock())

    def get_session(self, session_id):
        return self.state if session_id == "session" else None

    def _record_status(self, value):
        self.sidebar_status = value

    def _session_has_live_activity(self, session_id):
        return self.state.session_status in {"running", "finalizing"}

    def _workspace_dir_for_state(self, state):
        return ""

    def show_request(self, resolver, request_id="question", **extra):
        return self._show_inline_interaction_request(
            {"request_id": request_id, "session_id": "session", "kind": "text", **extra}, "session", resolver)


class InteractionUiLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        for name in ("log_ui_navigation", "log_sub_agent_runtime"):
            patcher = patch.object(main, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.host = InteractionHost()

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        self.app.processEvents()

    def test_transport_failure_preserves_input_and_does_not_close_card(self):
        client = DaemonClient()
        card = self.host.show_request(lambda value: client.respond_interaction("question", value))
        card.text_input.setText("保留我的回答")
        with patch.object(client, "_request", side_effect=ConnectionError("offline")):
            card._submit()
        self.assertIs(self.host.state.pending_interactions["question"], card)
        self.assertEqual(card.text_input.text(), "保留我的回答")
        self.assertTrue(card.submit_btn.isEnabled())
        self.assertIn("无法确认是否提交成功", card.validation_label.text())
        self.assertEqual(self.host.sidebar_status, "waiting")

    def test_only_explicit_acknowledgement_closes_successful_card(self):
        for index, acknowledgement in enumerate((None, False, {}, {"status": "error"},
                {"status": "ok", "resolved": False}, {"status": "ok", "resolved": "false"},
                True, {"status": "ok", "resolved": True})):
            with self.subTest(acknowledgement=acknowledgement):
                request_id = str(index)
                card = self.host.show_request(lambda value: acknowledgement, request_id)
                card.text_input.setText("answer")
                card._submit()
                accepted = acknowledgement is True or acknowledgement == {"status": "ok", "resolved": True}
                self.assertEqual(request_id not in self.host.state.pending_interactions, accepted)
                self.assertEqual(self.host.state.chat_layout.indexOf(card) == -1, accepted)

    def test_expired_ack_finishes_waiting_and_never_resubmits(self):
        resolver = Mock(return_value={"status": "ok", "resolved": False, "reason": "ended", "outcome": "timeout"})
        card = self.host.show_request(resolver)
        card.text_input.setText("too late")
        card._submit()
        self.assertEqual(self.host.state.pending_interactions, {})
        self.assertEqual(self.host.sidebar_status, "running")
        self.assertIn("请求已超时", card.validation_label.text())
        self.assertEqual(card.text_input.text(), "too late")
        self.assertTrue(card.text_input.isEnabled())
        self.assertTrue(card.text_input.isReadOnly())
        self.assertTrue(card.submit_btn.isHidden())
        card.restore_after_resolution_error("late network failure")
        card._submit()
        self.assertEqual(resolver.call_count, 1)
        self.assertIn("请求已超时", card.validation_label.text())

    def test_late_failure_cannot_resurrect_request_finished_during_submission(self):
        def resolve(value):
            self.host.handle_interaction_finished({"session_id": "session", "run_id": "run",
                "request_id": "question", "status": "timeout"})
            raise ConnectionError("late network failure")
        card = self.host.show_request(resolve)
        card.text_input.setText("keep")
        card._submit()
        self.assertEqual(self.host.state.pending_interactions, {})
        self.assertIn("请求已超时", card.validation_label.text())
        self.assertFalse(card.submit_btn.isEnabled())

    def test_real_timeout_clears_card_without_tool_result_or_user_click(self):
        service = InteractionService()
        service.interaction_requested.connect(self.host.handle_interaction_request, Qt.QueuedConnection)
        service.interaction_finished.connect(self.host.handle_interaction_finished, Qt.QueuedConnection)
        ready = threading.Event()
        release = threading.Event()
        def hold_request(request):
            ready.set()
            release.wait(3)
        service.interaction_requested.connect(hold_request, Qt.DirectConnection)
        results = []
        with patch.object(main, "bridge", service), patch("core.interaction.interaction_service", service):
            thread = threading.Thread(target=lambda: results.append(ask_user(
                "delete?", {"session_id": "session"}, timeout_seconds=0.01)))
            thread.start()
            try:
                self.assertTrue(ready.wait(2))
                self.app.processEvents()
                card = next(iter(self.host.state.pending_interactions.values()))
                self.assertEqual(self.host.sidebar_status, "waiting")
            finally:
                release.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.app.processEvents()
        self.assertEqual(results, [False])
        self.assertEqual(self.host.state.pending_interactions, {})
        self.assertIn("请求已超时", card.validation_label.text())
        self.host.set_session_status("completed", "session")
        self.assertEqual(self.host.sidebar_status, "idle")

    def test_terminal_run_clears_only_its_own_requests(self):
        first = self.host.show_request(Mock(), "old", metadata={"run_id": "old-run"})
        current = self.host.show_request(Mock(), "new", metadata={"run_id": "run"})
        self.host._finish_session_interactions(self.host.state, run_id="old-run")
        self.assertNotIn("old", self.host.state.pending_interactions)
        self.assertIn("new", self.host.state.pending_interactions)
        self.assertTrue(current.submit_btn.isEnabled())
        self.host.set_session_status("completed", "session")
        self.assertEqual(self.host.state.pending_interactions, {})
        self.assertEqual(self.host.sidebar_status, "idle")

    def test_waiting_notifications_end_with_their_request_including_queued_ones(self):
        self.host.current_session_id = "foreground"
        self.host.main_container = self.host
        self.host._system_toast_queue = []
        self.host._visible_system_toasts = []
        for name in ("add_system_toast", "_show_next_system_toast", "_position_active_system_toast",
                     "_system_toast_target_position", "_system_toast_duration", "_dismiss_system_toast"):
            setattr(self.host, name, getattr(MainWindow, name).__get__(self.host))
        count = main.DesignTokens.toast_max_visible + 1
        for index in range(count):
            self.host.show_request(Mock(), str(index))
        self.assertEqual(len(self.host._visible_system_toasts), count - 1)
        self.assertEqual(self.host._system_toast_queue[0]["request_id"], str(count - 1))
        self.host._finish_inline_interaction(self.host.state, str(count - 1), "timeout")
        self.assertEqual(self.host._system_toast_queue, [])
        self.host._finish_inline_interaction(self.host.state, "0", "timeout")
        remaining = {toast.interaction_request_id for toast in self.host._visible_system_toasts}
        self.assertEqual(remaining, {str(i) for i in range(1, count - 1)})

    def test_foreign_duplicate_and_late_events_do_not_change_new_request(self):
        card = self.host.show_request(Mock(), "question")
        for session, run in (("other", "run"), ("session", "old-run")):
            self.host.handle_interaction_finished({"session_id": session, "run_id": run,
                "request_id": "question", "status": "timeout"})
        self.assertIs(self.host.state.pending_interactions["question"], card)
        ended = {"session_id": "session", "run_id": "run", "request_id": "question", "status": "timeout"}
        self.host.handle_interaction_finished(ended)
        self.host.handle_interaction_finished(ended)
        self.assertEqual(self.host.state.pending_interactions, {})
        self.host.set_session_status("completed", "session")
        self.host.handle_interaction_request({"session_id": "session", "request_id": "late",
            "metadata": {"run_id": "run"}})
        self.assertEqual(self.host.state.pending_interactions, {})

    def test_daemon_replayed_end_dispatches_once_and_restores_idle(self):
        worker = DaemonStreamWorker(None, "session", "", request_id="run")
        worker.interaction_signal.connect(self.host.handle_daemon_interaction_request)
        worker.interaction_finished_signal.connect(self.host.handle_interaction_finished)
        requested = {"type": "interaction_request", "session_id": "session", "run_id": "run", "sequence": 1,
            "data": {"session_id": "session", "request_id": "question", "kind": "approval"}}
        ended = {"type": "interaction_finished", "session_id": "session", "run_id": "run", "sequence": 2,
            "data": {"session_id": "session", "run_id": "run", "request_id": "question", "status": "timeout"}}
        worker._dispatch_stream_message(requested)
        self.assertEqual(self.host.sidebar_status, "waiting")
        worker._dispatch_stream_message(ended)
        worker._dispatch_stream_message(requested)
        worker._dispatch_stream_message(ended)
        self.assertEqual(self.host.state.pending_interactions, {})
        self.assertEqual(len(self.host.findChildren(InlineInteractionCard)), 1)
        self.host.set_session_status("completed", "session")
        self.assertEqual(self.host.sidebar_status, "idle")
        worker.deleteLater()


if __name__ == "__main__":
    unittest.main()
