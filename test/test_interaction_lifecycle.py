"""Request ownership, terminal delivery and acknowledgement regression tests."""
import os
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")


from core.interaction import InteractionService, ask_user


class InteractionLifecycleTests(unittest.TestCase):
    def service(self):
        service = InteractionService()
        ended = []
        service.interaction_finished.subscribe(ended.append)
        return service, ended

    def test_all_kinds_preserve_answers_and_emit_one_receipt(self):
        for kind, value, extra in (
            ("approval", True, {}), ("approval", False, {}), ("text", "private answer", {}),
            ("choice", "a", {"options": ["a", "b"]}),
            ("multi_choice", ["a", "b"], {"options": ["a", "b"]}),
            ("questionnaire", {"q": {"text": "private answer"}},
             {"questions": [{"id": "q", "question": "question"}]}),
        ):
            with self.subTest(kind=kind, value=value):
                service, ended = self.service()
                service.interaction_requested.subscribe(
                    lambda request: self.assertIs(service.resolve_request(request["request_id"], value), True),

                )
                result = service.create_request("session", kind, "question", timeout_seconds=86400,
                                                metadata={"run_id": "run"}, **extra)
                self.assertEqual(len(ended), 1)
                receipt = ended[0].pop("response")
                self.assertEqual(ended[0], {
                    "session_id": "session", "run_id": "run", "request_id": result["request_id"],
                    "status": result["status"], "approved": result["approved"], "answered": True,
                })
                self.assertEqual(receipt, result)
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
        service.interaction_requested.subscribe(capture)
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
        service.interaction_requested.subscribe(lambda _request: ready.set())
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
                service.interaction_requested.subscribe(capture)
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
        service.interaction_finished.subscribe(finished)
        thread = threading.Thread(target=lambda: calls.append(service.create_request(
            "session", "approval", "question", timeout_seconds=0.01)), daemon=True)
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive(), "Terminal notification must not hold the interaction lock")
        self.assertEqual(calls[:2], [None, False])
        self.assertEqual(calls[2]["status"], "timeout")
        self.assertEqual(len(ended), 1)


if __name__ == "__main__":
    unittest.main()
