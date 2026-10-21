import os
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication

from core.execution_authorization import ExecutionAuthorization
from core.interaction import InteractionService, interaction_service
from core.tool_tasks import ToolTaskManager
import test_core


class BackgroundInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = test_core.TestDaemonInteractionRoundtrip.setUp
    tearDown = test_core.TestDaemonInteractionRoundtrip.tearDown

    def test_background_question_streams_to_owner_and_accepts_answer(self):
        class Worker(QThread):
            thinking_signal = Signal(str)
            content_signal = Signal(str)
            tool_call_signal = Signal(dict)
            tool_result_signal = Signal(dict)
            observability_signal = Signal(dict)
            agent_state_signal = Signal(dict)
            output_signal = Signal(str)
            finished_signal = Signal(object)

            def __init__(self, messages, config, workspace_dir, **kwargs):
                super().__init__()
                self.agent_manager = None
                self.request_id = kwargs["request_id"]
                self.execution_authorization = ExecutionAuthorization.start(
                    config, kwargs["session_id"], self.request_id, workspace_dir)
                self.tool_tasks = ToolTaskManager(kwargs["session_id"], self.request_id)

            def run(self):
                try:
                    def ask():
                        # Foreign runs and sessions must never receive this card.
                        for session_id, run_id in (("other-session", self.request_id),
                                                   (self.tool_tasks.session_id, "old-run")):
                            interaction_service.interaction_requested.emit({
                                "session_id": session_id, "request_id": "wrong", "kind": "text",
                                "metadata": {"run_id": run_id}})
                            interaction_service.interaction_finished.emit({
                                "session_id": session_id, "run_id": run_id,
                                "request_id": "wrong", "status": "timeout"})
                        return interaction_service.create_request(self.tool_tasks.session_id,
                            "text", "选择运行环境", timeout_seconds=3, source_tool="request_user_input")
                    task, wait = self.tool_tasks.submit("ask", "request_user_input", {}, "call", ask)
                    answer = self.tool_tasks.await_initial(task, wait)
                    self.finished_signal.emit({"content": answer.get("text"), "answer": answer})
                finally:
                    self.tool_tasks.close()

        chunks = []
        with patch("core.daemon.LLMWorker", Worker):
            for chunk in self.client.send_message_stream("daemon-session-test", "向我提问",
                    workspace_dir=self.temp_dir, run_context={"mode": "execution"}, writer_owner="ui:4242"):
                chunks.append(chunk)
                if chunk.get("type") == "interaction_request":
                    request = chunk["data"]
                    self.assertNotEqual(request["request_id"], "wrong")
                    self.assertEqual(request["session_id"], "daemon-session-test")
                    self.assertEqual(request["metadata"]["run_id"], chunk["run_id"])
                    ack = self.client.respond_interaction(request["request_id"], "使用本地环境")
                    self.assertTrue(ack["resolved"])
        self.assertEqual(sum(chunk.get("type") == "interaction_request" for chunk in chunks), 1)
        ended = [chunk["data"] for chunk in chunks if chunk.get("type") == "interaction_finished"]
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0]["request_id"], request["request_id"])
        self.assertEqual(ended[0]["status"], "completed")
        self.assertTrue(ended[0]["answered"])
        final = next(chunk["result"] for chunk in chunks if chunk.get("type") == "final")
        self.assertEqual(final["content"], "使用本地环境")
        self.assertEqual(final["answer"]["status"], "completed")

    def test_legacy_timeout_is_durable_and_replayed_without_tool_response_payload(self):
        class Worker(QThread):
            thinking_signal = Signal(str)
            content_signal = Signal(str)
            tool_call_signal = Signal(dict)
            tool_result_signal = Signal(dict)
            observability_signal = Signal(dict)
            agent_state_signal = Signal(dict)
            output_signal = Signal(str)
            finished_signal = Signal(object)

            def __init__(self, messages, config, workspace_dir, **kwargs):
                super().__init__()
                self.agent_manager = None
                self.request_id = kwargs["request_id"]
                self.execution_authorization = ExecutionAuthorization.start(
                    config, kwargs["session_id"], self.request_id, workspace_dir)
                self.tool_tasks = ToolTaskManager(kwargs["session_id"], self.request_id)

            def run(self):
                from core.interaction import ask_user
                try:
                    task, wait = self.tool_tasks.submit("delete", "delete_file", {}, "call",
                        lambda: ask_user("Confirm delete recursively: 'repo'?", timeout_seconds=0.05))
                    result = self.tool_tasks.await_initial(task, wait)
                    self.finished_signal.emit({"content": "kept files", "approved": result})
                finally:
                    self.tool_tasks.close()

        with patch("core.daemon.LLMWorker", Worker):
            chunks = list(self.client.send_message_stream("daemon-session-test", "test timeout",
                workspace_dir=self.temp_dir, run_context={"mode": "execution"}, writer_owner="ui:4242"))
        self.assertFalse(any(chunk.get("type") == "tool_result" for chunk in chunks))
        requested = next(chunk for chunk in chunks if chunk.get("type") == "interaction_request")
        ended = [chunk for chunk in chunks if chunk.get("type") == "interaction_finished"]
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0]["data"]["request_id"], requested["data"]["request_id"])
        self.assertEqual(ended[0]["data"]["status"], "timeout")
        self.assertFalse(ended[0]["data"]["answered"])
        self.assertFalse(next(chunk["result"] for chunk in chunks if chunk.get("type") == "final")["approved"])
        # A client which missed the completion can recover it through the
        # existing cursor API; no separate interaction database is required.
        replay = self.client.attach_run("daemon-session-test", requested["run_id"],
                                       starting_after=requested["sequence"])
        replayed = [event for event in replay["events"] if event["type"] == "interaction_finished"]
        self.assertEqual([event["payload"]["data"] for event in replayed], [ended[0]["data"]])
        self.assertLess(ended[0]["sequence"], next(chunk["sequence"] for chunk in chunks if chunk.get("type") == "final"))
        late = self.client.respond_interaction(requested["data"]["request_id"], True)
        self.assertFalse(late["resolved"])
        self.assertEqual(late["reason"], "ended")

    def test_stop_cancels_background_question_and_keeps_other_session_pending(self):
        service = InteractionService()
        owners = [ToolTaskManager("one", "run-one"), ToolTaskManager("two", "run-two")]
        tasks = []
        try:
            for owner in owners:
                task, _ = owner.submit("ask", "request_user_input", {}, "call",
                    lambda owner=owner: service.create_request(owner.session_id, "text", "question", timeout_seconds=3))
                tasks.append(task)
            from test_tool_tasks import eventually
            eventually(lambda: service.get_pending_request("one") and service.get_pending_request("two"))
            owners[0].close()
            self.assertTrue(tasks[0].done.wait(1))
            self.assertEqual(tasks[0].result["status"], "cancelled")
            self.assertIsNone(service.get_pending_request("one"))
            self.assertFalse(tasks[1].done.is_set())
            request = service.get_pending_request("two")
            self.assertTrue(service.resolve_request(request["request_id"], "second answer"))
            self.assertTrue(tasks[1].done.wait(1))
            self.assertEqual(tasks[1].result["text"], "second answer")
        finally:
            for owner in owners:
                owner.close()
            for task in tasks:
                task.done.wait(1)

    def test_daemon_release_includes_background_tool_cleanup(self):
        done = threading.Event()
        class Worker:
            request_id = "cleanup-run"
            from types import SimpleNamespace
            tool_tasks = SimpleNamespace(tasks={"slow": SimpleNamespace(done=done)})
            def stop(self): pass
            def wait(self, timeout): return True
        self.state.set_active_worker("cleanup-session", Worker(), run_id="cleanup-run")
        result = self.state.stop_session("cleanup-session", expected_run_id="cleanup-run",
            wait_for_release=True, release_timeout_ms=30)
        self.assertFalse(result["released"])
        done.set()
        result = self.state.stop_session("cleanup-session", expected_run_id="cleanup-run",
            wait_for_release=True, release_timeout_ms=100)
        self.assertTrue(result["released"])


if __name__ == "__main__":
    unittest.main()
