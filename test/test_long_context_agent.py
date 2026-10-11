import copy
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from core.agent import LLMWorker
from core.chat_save_queue import ChatSaveRequest, ChatSaveWorker
from core.chat_storage import ChatStorage
from core.execution_authorization import ExecutionAuthorization
from core.runtime_journal import RuntimeJournal
from core.tool_registry import ToolRegistry
from test_agent_bootstrap import ConfigStub, SkillStub
from test_long_context import checkpoint


class ContextSkills(SkillStub):
    def __init__(self):
        super().__init__()
        self.tool_registry = ToolRegistry()
        self.tools, self.tool_records = {}, {}

    def get_tool_definitions(self, **kwargs):
        return super().get_tool_definitions(**kwargs) + self.tool_registry.definitions()

    def call_tool(self, name, args, context=None):
        if name in self.tools:
            return self.tools[name](**args, _context=context)
        self.calls.append((name, copy.deepcopy(args)))
        return {"status": "success", "content": "evidence " * 350}


class ContextProvider:
    provider_name = "OpenAI"
    model_name = "test-model"
    base_url = "https://example.test/v1"
    api_protocol = "chat_completions"
    thinking_enabled = False

    def __init__(self, rounds):
        self.rounds = rounds
        self.main_requests, self.summary_requests = [], []

    def chat_stream(self, messages, tools=None, prompt_cache_key=None, request_context=None):
        if request_context.get("tools_disabled"):
            self.summary_requests.append(copy.deepcopy(messages))
            assert tools == []
            yield {"type": "content", "content": json.dumps(checkpoint(["u0"], goal="Finish without sending email"))}
            yield {"type": "usage", "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}}
            return
        index = len(self.main_requests)
        self.main_requests.append(copy.deepcopy({"messages": messages, "tools": tools}))
        if index < self.rounds:
            yield {"type": "content", "content": f"Step {index}: " + "inspected evidence " * 500}
            yield {"type": "tool_call", "index": 0, "id": f"call-{index}", "function": {
                "name": "read_file", "arguments": json.dumps({"step": index})}}
        else:
            yield {"type": "content", "content": "Task complete, email was not sent."}
        yield {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}


class TestLongContextAgent(unittest.TestCase):
    def run_agent(self, enabled, rounds=0):
        from main import MainWindow
        self.app = QApplication.instance() or QApplication([])
        profile = {"id": "test", "model_name": "test-model", "base_url": "https://example.test/v1",
                   "provider": "openai", "api_protocol": "chat_completions",
                   "context_window_tokens": 32000, "max_output_tokens": 4096}
        initial = [{"id": "u0", "role": "user", "content": "Finish without sending email."},
                   {"id": "old", "role": "assistant", "content": "older findings " * 8000},
                   {"id": "recent", "role": "assistant", "content": "Ready to continue."},
                   {"id": "u1", "role": "user", "content": "Continue the task."}]
        provider, skills = ContextProvider(rounds), ContextSkills()
        events, results = [], []
        with tempfile.TemporaryDirectory() as directory:
            storage = ChatStorage(os.path.join(directory, "chat_history.sqlite"))
            storage.save_conversation_safely("s", initial)
            journal = RuntimeJournal(directory)
            journal.begin_run("s", "run", writer_owner="ui:test", base_messages=initial,
                              extra={"user_message_id": "u1"})
            queue = ChatSaveWorker(storage.db_path, debounce_ms=5000)
            queue.start()
            state = SimpleNamespace(session_id="s", active_turn_request_id="run", messages=copy.deepcopy(initial))
            ui = SimpleNamespace(chat_save_worker=queue,
                _history_writer_owner_for_session=lambda sid: "ui:test",
                _prepare_generated_messages_for_turn=lambda state, messages: (copy.deepcopy(messages), 0),
                _merge_generated_messages=lambda old, new: MainWindow._merge_generated_messages(None, old, new))
            revision = [0]
            def save(sid):
                revision[0] += 1
                return queue.enqueue(ChatSaveRequest(sid, copy.deepcopy(state.messages), "", "running", {}, 0, revision[0]))
            ui.save_chat_history = save
            worker = None
            def event(value):
                events.append(value)
                if value.get("type") == "context_storage_request":
                    MainWindow._handle_context_storage_request(ui, state, value)
                if value.get("phase") == "failed":
                    worker.is_stopped = True  # A failed test must never wait for manual input.
            try:
                with patch("core.agent.SkillManager", return_value=skills), \
                     patch("core.agent.LLMFactory.create_provider", return_value=provider), \
                     patch.object(LLMWorker, "_bind_agent_manager"), \
                     patch.object(LLMWorker, "_build_stable_system_prompt", return_value="Stable prompt"), \
                     patch.object(LLMWorker, "_build_runtime_context_prompt", return_value="Current runtime"), \
                     patch("core.agent.get_runtime_snapshot", return_value={}):
                    worker = LLMWorker(initial, ConfigStub(directory, profile), directory,
                        session_id="s", turn_id="1", request_id="run",
                        run_context={"selected_model_profile": profile,
                                     "desktop_long_context": {"enabled": enabled}},
                        execution_authorization=ExecutionAuthorization("s", "run", True, ""))
                    worker.observability_signal.connect(event)
                    worker.finished_signal.connect(results.append)
                    worker.run()
                self.assertEqual(len(results), 1)
                self.assertNotIn("error", results[0], str([e for e in events if e.get("phase") == "failed"]))
                self.assertTrue(queue.flush("s", 2000))
                active = storage.get_context_window("s")
                return provider, skills, events, results[0], active
            finally:
                queue.stop_worker(2000)

    def test_default_off_has_no_checkpoint_requests_or_tools(self):
        provider, skills, events, result, active = self.run_agent(False)
        self.assertEqual(provider.summary_requests, [])
        self.assertIsNone(active)
        self.assertEqual(len(provider.main_requests), 1)
        self.assertFalse(any(t["function"]["name"].startswith("history_") for t in provider.main_requests[0]["tools"]))
        self.assertFalse(any(e.get("type", "").startswith("context_") for e in events))

    def test_real_loop_three_windows_tools_once_and_prefix_checks(self):
        provider, skills, events, result, active = self.run_agent(True, rounds=18)
        self.assertGreaterEqual(active["ordinal"], 3)
        self.assertEqual(len(skills.calls), 18)
        self.assertEqual(len({args["step"] for _, args in skills.calls}), 18)
        self.assertIn("Task complete", result["content"])
        self.assertTrue(all(e["ok"] for e in events if e.get("type") == "conversation_prefix_check"))
        self.assertTrue(any(e.get("type") == "llm_usage" and e["usage"].get("purpose") == "context_checkpoint" for e in events))
        self.assertTrue(any(e.get("type") == "context_capacity" and e["capacity"].get("phase") == "finished" for e in events))
        self.assertFalse(any(m.get("meta", {}).get("kind") == "context_checkpoint" for m in result["generated_messages"]))


if __name__ == "__main__":
    unittest.main()
