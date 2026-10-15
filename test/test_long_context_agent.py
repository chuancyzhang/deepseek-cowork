import copy
import json
import os
import tempfile
import threading
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
    def __init__(self, tool_result=None):
        super().__init__()
        self.tool_registry = ToolRegistry()
        self.tools, self.tool_records = {}, {}
        self.tool_result = tool_result

    def get_tool_definitions(self, **kwargs):
        return super().get_tool_definitions(**kwargs) + self.tool_registry.definitions()

    def call_tool(self, name, args, context=None):
        if name in self.tools:
            return self.tools[name](**args, _context=context)
        self.calls.append((name, copy.deepcopy(args)))
        if self.tool_result is not None:
            return copy.deepcopy(self.tool_result)
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
        self.main_request_options, self.summary_request_options = [], []

    def chat_stream(self, messages, tools=None, prompt_cache_key=None, request_context=None):
        output_options = {key: value for key, value in request_context.items() if key == "max_output_tokens"}
        if request_context.get("tools_disabled"):
            self.summary_requests.append(copy.deepcopy(messages))
            self.summary_request_options.append(output_options)
            assert tools == []
            yield {"type": "content", "content": json.dumps(checkpoint(["u0"], goal="Finish without sending email"))}
            yield {"type": "usage", "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}}
            return
        index = len(self.main_requests)
        self.main_requests.append(copy.deepcopy({"messages": messages, "tools": tools}))
        self.main_request_options.append(output_options)
        if index < self.rounds:
            yield {"type": "content", "content": f"Step {index}: " + "inspected evidence " * 500}
            yield {"type": "tool_call", "index": 0, "id": f"call-{index}", "function": {
                "name": "read_file", "arguments": json.dumps({"step": index})}}
        else:
            yield {"type": "content", "content": "Task complete, email was not sent."}
        yield {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}


class TestLongContextAgent(unittest.TestCase):
    def run_agent(self, enabled, rounds=0, provider=None, tool_result=None,
                  known_capacity=True, target_tokens=300000, attachments=None, attachment_message_id="u1",
                  profile_overrides=None):
        from main import MainWindow
        self.app = QApplication.instance() or QApplication([])
        profile = {"id": "test", "model_name": "test-model", "base_url": "https://example.test/v1",
                   "provider": "openai", "api_protocol": "chat_completions",
                   "context_window_tokens": 32000, "max_output_tokens": 4096}
        if not known_capacity:
            profile.pop("context_window_tokens")
            profile.pop("max_output_tokens")
        profile.update(profile_overrides or {})
        initial = [{"id": "u0", "role": "user", "content": "Finish without sending email."},
                   {"id": "old", "role": "assistant", "content": "older findings " * 8000},
                   {"id": "recent", "role": "assistant", "content": "Ready to continue."},
                   {"id": "u1", "role": "user", "content": "Continue the task."}]
        if attachments:
            next(m for m in initial if m["id"] == attachment_message_id)["content_parts"] = copy.deepcopy(attachments)
        provider, skills = provider or ContextProvider(rounds), ContextSkills(tool_result)
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
                                     "desktop_long_context": {"enabled": enabled, "target_tokens": target_tokens}},
                        execution_authorization=ExecutionAuthorization("s", "run", True, ""))
                    worker.observability_signal.connect(event)
                    worker.finished_signal.connect(results.append)
                    worker.run()
                self.assertEqual(len(results), 1)
                self.assertNotIn("error", results[0], str([e for e in events if e.get("phase") == "failed"]))
                self.assertTrue(queue.flush("s", 2000))
                active = storage.get_context_window("s")
                if attachments:
                    saved = next(m for m in storage.get_messages("s") if m["id"] == attachment_message_id)
                    self.assertEqual(saved["content_parts"], attachments)
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

    def test_enabled_unconfigured_model_reaches_provider_for_both_targets(self):
        for target in (300000, 1000000):
            with self.subTest(target=target):
                provider, skills, events, result, active = self.run_agent(
                    True, known_capacity=False, target_tokens=target)
                self.assertEqual(len(provider.main_requests), 1)
                self.assertEqual(provider.summary_requests, [])
                self.assertIn("Task complete", result["content"])
                snapshots = [e["capacity"] for e in events if e.get("type") == "context_capacity"]
                self.assertTrue(snapshots)
                self.assertTrue(all(s["effective_tokens"] == target for s in snapshots))
                self.assertTrue(all(s["model_tokens"] is None for s in snapshots))
                self.assertIsNone(active)

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

    def test_default_reserve_does_not_limit_generation_before_or_after_rollover(self):
        for known_capacity in (True, False):
            with self.subTest(known_capacity=known_capacity):
                provider, skills, events, result, active = self.run_agent(
                    True, rounds=2, known_capacity=known_capacity, profile_overrides={"max_output_tokens": None})
                self.assertEqual(provider.main_request_options, [{}, {}, {}])
                self.assertEqual(bool(provider.summary_requests), known_capacity)
                self.assertTrue(all(options == {"max_output_tokens": 8192} for options in provider.summary_request_options))
                self.assertTrue(all(e["ok"] for e in events if e.get("type") == "conversation_prefix_check"))
                self.assertEqual(len(skills.calls), 2)
                self.assertIn("Task complete", result["content"])

    def test_long_response_reaches_agent_through_actual_provider_without_default_wire_limit(self):
        import test_deepseek_provider as helpers
        output = "完整页面内容。" * 2000 + "页面结束。"
        for protocol in ("chat_completions", "responses"):
            for limit in (None, 32768):
                with self.subTest(protocol=protocol, limit=limit):
                    provider, client = helpers.TestOpenAIProviderDeepSeek._build_provider(self,
                        base_url="https://openrouter.ai/api/v1", model_name="z-ai/glm-5.3-flash",
                        api_protocol=protocol, reasoning_effort="max")
                    if protocol == "chat_completions":
                        create = client.chat.completions.create
                        create.return_value = [SimpleNamespace(id="response", usage=None, choices=[SimpleNamespace(
                            index=0, finish_reason=None, delta=SimpleNamespace(content=output,
                                reasoning_content=None, tool_calls=None))]),
                            SimpleNamespace(id="response", usage={"prompt_tokens": 12081, "completion_tokens": 12000,
                                "total_tokens": 24081}, choices=[SimpleNamespace(index=0, finish_reason="stop",
                                    delta=SimpleNamespace(content=None, reasoning_content=None, tool_calls=None))])]
                        limit_key = "max_tokens"
                    else:
                        create = client.responses.create
                        create.return_value = [SimpleNamespace(type="response.output_text.delta", delta=output),
                            SimpleNamespace(type="response.completed", response=SimpleNamespace(id="response", status="completed",
                                output=[{"type": "message", "id": "reply", "role": "assistant", "status": "completed",
                                         "content": [{"type": "output_text", "text": output}]}],
                                usage={"input_tokens": 12081, "output_tokens": 12000, "total_tokens": 24081}))]
                        limit_key = "max_output_tokens"
                    _, _, events, result, _ = self.run_agent(True, provider=provider, known_capacity=False,
                        profile_overrides={"model_name": provider.model_name, "base_url": provider.base_url,
                                           "api_protocol": protocol, "max_output_tokens": limit})
                    create.assert_called_once()
                    params = create.call_args.kwargs
                    if limit is None:
                        self.assertFalse({"max_tokens", "max_completion_tokens", "max_output_tokens"} & params.keys())
                    else:
                        self.assertEqual(params[limit_key], limit)
                    self.assertEqual(result["content"], output)
                    self.assertTrue(any(e.get("type") == "llm_usage" and e["usage"]["output_tokens"] == 12000 for e in events))
                    self.assertTrue(all(e["ok"] for e in events if e.get("type") == "conversation_prefix_check"))

    def test_file_attachments_allow_followup_and_rollover_with_unchanged_wire_content(self):
        from core.llm.providers import OpenAIProvider
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "recording.wav")
            with open(path, "wb") as handle:
                handle.write(b"RIFF\x00\xffWAVE")
            parts = [{"type": "input_file", "path": path, "name": "recording.wav"}]
            for known_capacity, message_id in ((False, "u0"), (True, "u1")):
                with self.subTest(rollover=known_capacity):
                    provider, skills, events, result, active = self.run_agent(
                        True, rounds=1, known_capacity=known_capacity,
                        attachments=parts, attachment_message_id=message_id)
                    self.assertEqual(len(provider.main_requests), 2)
                    self.assertEqual(len(skills.calls), 1)
                    self.assertIn("Task complete", result["content"])
                    self.assertEqual(bool(active), known_capacity)
                    self.assertTrue(all(e["ok"] for e in events if e.get("type") == "conversation_prefix_check"))
                    for request in provider.main_requests:
                        message = next(m for m in request["messages"] if m.get("id") == message_id)
                        self.assertEqual(message["content_parts"], parts)
                        encoder = OpenAIProvider.__new__(OpenAIProvider)
                        encoder.supports_vision = False
                        encoder.model_name = "gpt-6.1-sol"
                        encoder.base_url = "https://example.test/v1"
                        wire = encoder._prepare_responses_input([message])
                        text = "\n".join(p.get("text", "") for item in wire for p in item.get("content", []))
                        self.assertIn(f"Path: {path}", text)
                        self.assertIn("not treated as plain text", text)
                        self.assertNotIn("RIFF", text)

    def test_browser_image_result_reaches_next_model_request_with_context_enabled(self):
        from PIL import Image
        from core.llm.providers import OpenAIProvider
        from core.tool_images import persist_tool_image
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "screenshot.png")
            Image.new("RGB", (1920, 1080), "white").save(path)
            part = persist_tool_image(path, directory, "s")
            browser_result = {"status": "success", "content": "Page text", "content_parts": [part]}
            for model in ("gpt-5.6-sol", "unlisted-vision-model"):
                with self.subTest(model=model):
                    provider = ContextProvider(1)
                    provider.model_name = model
                    provider, skills, events, result, active = self.run_agent(True, provider=provider, tool_result=browser_result)
                    self.assertEqual(len(provider.main_requests), 2)
                    message = next(m for m in provider.main_requests[-1]["messages"] if m.get("role") == "tool")
                    self.assertEqual(message["content_parts"], [part])
                    # Exercise the actual Responses wire projection as well as
                    # the Agent loop; there must still be an image in the result.
                    encoder = OpenAIProvider.__new__(OpenAIProvider)
                    encoder.supports_vision = True
                    wire = encoder._prepare_responses_input([message])
                    image = next(p for p in wire[0]["output"] if p["type"] == "input_image")
                    self.assertTrue(image["image_url"].startswith("data:image/png;base64,"))
                    self.assertEqual(image["detail"], part["detail"])
                    capacity = [e["capacity"] for e in events if e.get("type") == "context_capacity"][-1]
                    self.assertEqual(capacity["status"], "available")
                    self.assertEqual(capacity["unestimated_images"], int(model == "unlisted-vision-model"))
                    self.assertTrue(all(e["ok"] for e in events if e.get("type") == "conversation_prefix_check"))

    def test_managed_history_tools_read_saved_results_after_rollover(self):
        from core.context_window import ContextWindowManager
        owner = threading.get_ident()
        barriers = []
        original = ContextWindowManager.persist_history

        def persist(manager, *args, **kwargs):
            barriers.append(threading.get_ident())
            return original(manager, *args, **kwargs)

        class HistoryProvider(ContextProvider):
            def chat_stream(inner, messages, tools=None, prompt_cache_key=None, request_context=None):
                if request_context.get("tools_disabled"):
                    yield from super().chat_stream(messages, tools, prompt_cache_key, request_context)
                    return
                inner.main_requests.append(copy.deepcopy(messages))
                turn = len(inner.main_requests)
                if turn == 1:
                    name, args = "read_file", {"step": 0}
                elif turn == 2:
                    name, args = "history_search", {"query": "evidence"}
                elif turn == 3:
                    found = next(m["result_obj"] for m in reversed(messages) if m.get("role") == "tool")
                    self.assertIn("matches", found)
                    match = next(m for m in found["matches"] if m["role"] == "tool")
                    name, args = "history_read", {"message_id": match["id"], "length": 100}
                else:
                    result = next(m["result_obj"] for m in reversed(messages) if m.get("role") == "tool")
                    self.assertIn("evidence", result["content"])
                    self.assertEqual(len(result["content"]), 100)
                    yield {"type": "content", "content": "Saved evidence recovered."}
                    return
                yield {"type": "tool_call", "index": 0, "id": f"history-{turn}", "function": {
                    "name": name, "arguments": json.dumps(args)}}

        with patch.object(ContextWindowManager, "persist_history", persist):
            provider, skills, events, result, active = self.run_agent(True, provider=HistoryProvider(0))
        self.assertIsNotNone(active)
        self.assertEqual(len(provider.main_requests), 4)
        self.assertEqual(result["content"], "Saved evidence recovered.")
        self.assertGreaterEqual(len(barriers), 3)  # rollover + both history queries
        self.assertEqual(set(barriers), {owner})
        self.assertTrue(all(e["ok"] for e in events if e.get("type") == "conversation_prefix_check"))


if __name__ == "__main__":
    unittest.main()
