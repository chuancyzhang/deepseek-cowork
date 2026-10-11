import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import test_deepseek_provider as helpers


class TestContextProviderContract(unittest.TestCase):
    def test_history_save_failure_does_not_start_background_reader(self):
        from core.agent import LLMWorker
        manager = SimpleNamespace(submit=Mock())
        worker = SimpleNamespace(
            _ensure_tool_tasks=lambda: manager,
            bootstrap=SimpleNamespace(active=False),
            context_window_manager=SimpleNamespace(persist_history=Mock(side_effect=RuntimeError("save failed"))))
        with self.assertRaisesRegex(RuntimeError, "save failed"):
            LLMWorker._run_managed_tool(worker, "history_read", {"message_id": "m"},
                {"tool_call_id": "call"}, "execution", {"read_only": True}, None)
        manager.submit.assert_not_called()

    def test_summary_disables_native_search_and_image_tools_with_output_limit(self):
        provider, client = helpers.TestOpenAIProviderDeepSeek._build_provider(self,
            api_protocol="responses", supports_image_generation=True)
        self.assertTrue(provider._prepare_responses_tools([]))
        client.responses.create.return_value = [SimpleNamespace(
            type="response.completed", response=SimpleNamespace(id="r", status="completed", output=[{
                "type": "message", "id": "m", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "ok"}]}], usage=None))]
        list(provider._responses_stream([{"role": "user", "content": "summarize"}], tools=[],
            request_context={"tools_disabled": True, "max_output_tokens": 4096}))
        params = client.responses.create.call_args.kwargs
        self.assertNotIn("tools", params)
        self.assertEqual(params["max_output_tokens"], 4096)

    def test_chat_output_limit_is_opt_in(self):
        provider, client = helpers.TestOpenAIProviderDeepSeek._build_provider(self)
        client.chat.completions.create.return_value = [SimpleNamespace(id="r", usage=None, choices=[SimpleNamespace(
            index=0, finish_reason="stop", delta=SimpleNamespace(content="ok", reasoning_content=None, tool_calls=None))])]
        list(provider._chat_completions_stream([{"role": "user", "content": "hello"}],
                                             request_context={"max_output_tokens": 4096}))
        self.assertEqual(client.chat.completions.create.call_args.kwargs["max_tokens"], 4096)
        list(provider._chat_completions_stream([{"role": "user", "content": "hello"}]))
        self.assertNotIn("max_tokens", client.chat.completions.create.call_args.kwargs)

    def test_official_openai_uses_completion_limit_for_reasoning_models(self):
        provider, client = helpers.TestOpenAIProviderDeepSeek._build_provider(self,
            base_url="https://api.openai.com/v1", model_name="o3")
        client.chat.completions.create.return_value = [SimpleNamespace(id="r", usage=None, choices=[SimpleNamespace(
            index=0, finish_reason="stop", delta=SimpleNamespace(content="ok", reasoning_content=None, tool_calls=None))])]
        list(provider._chat_completions_stream([{"role": "user", "content": "hello"}],
                                             request_context={"max_output_tokens": 8192}))
        params = client.chat.completions.create.call_args.kwargs
        self.assertEqual(params["max_completion_tokens"], 8192)
        self.assertNotIn("max_tokens", params)

    def test_checkpoint_stream_setup_error_records_failed_attempt(self):
        from core.agent import LLMWorker
        records = []
        worker = SimpleNamespace(request_id="r", is_stopped=False,
            context_window_manager=SimpleNamespace(budget=SimpleNamespace(output_tokens=8192)),
            _record_provider_attempt=lambda identity, value: records.append((identity, value)),
            observability_signal=SimpleNamespace(emit=lambda event: None),
            _provider_chat_stream=Mock(side_effect=RuntimeError("setup failed")),
            _register_provider_stream=Mock(), _release_provider_stream=Mock())
        with self.assertRaisesRegex(RuntimeError, "setup failed"):
            LLMWorker._generate_context_checkpoint(worker, SimpleNamespace(), [], "w", 1, 0)
        self.assertEqual([value["status"] for _, value in records], ["running", "failed"])
        self.assertEqual(records[0][0], records[1][0])


if __name__ == "__main__":
    unittest.main()
