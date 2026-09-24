import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from core.data_security import begin_run, normalize_config
from core.llm.providers import OpenAIProvider, AnthropicProvider
from core.conversation_render import project_visible_messages


def enabled():
    return normalize_config({"enabled": True, "tokenize": True, "categories": {"email": True, "phone": True}})


class DataSecurityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch("bundled_plugins.data_security.events.append")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_chat_and_responses_hook_after_attachment_expansion(self):
        from test_deepseek_provider import TestOpenAIProviderDeepSeek
        helper = TestOpenAIProviderDeepSeek()
        self.addCleanup(helper.doCleanups)
        attachment = Path(self.temp.name) / "input.txt"
        attachment.write_text("alice@example.org", encoding="utf-8")
        for protocol in ("chat_completions", "responses"):
            with self.subTest(protocol=protocol):
                provider, client = helper._build_provider(base_url="https://example.test/v1", model_name="model", api_protocol=protocol)
                provider.prompt_cache_key_param = "prompt_cache_key"
                provider.data_security_run = begin_run(policy=enabled(), scope=protocol, data_dir=self.temp.name)
                client.chat.completions.create.return_value = [SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="done", reasoning_content=None, tool_calls=[]), finish_reason="stop")], usage=None)]
                client.responses.create.return_value = [SimpleNamespace(type="response.completed", response=SimpleNamespace(
                    id="resp", status="completed", output=[], usage=None))]
                messages = [{"role": "system", "content": "alice@example.org"},
                            {"role": "user", "content": "13800138000", "content_parts": [{"type": "input_file", "path": str(attachment)}]}]
                original = copy.deepcopy(messages)
                list(provider.chat_stream(messages, prompt_cache_key="stable"))
                self.assertEqual(messages, original)
                call = client.chat.completions.create if protocol == "chat_completions" else client.responses.create
                self.assertEqual(call.call_count, 1)
                payload = call.call_args.kwargs
                items = payload["messages" if protocol == "chat_completions" else "input"]
                self.assertIn("alice@example.org", str(items[0]))
                self.assertNotIn("alice@example.org", str(items[1]))
                self.assertNotIn("13800138000", str(items[1]))
                self.assertEqual(payload.get("prompt_cache_key"), "stable")

    def test_anthropic_sdk_receives_projected_user_and_original_auth_system(self):
        module = ModuleType("anthropic")
        client = MagicMock()
        module.Anthropic = MagicMock(return_value=client)
        with patch.dict("sys.modules", {"anthropic": module}):
            provider = AnthropicProvider("real-auth-test", "https://example.test", "model")
        provider.data_security_run = begin_run(policy=enabled(), scope="anthropic", data_dir=self.temp.name)
        client.messages.stream.return_value.__enter__.return_value = iter([SimpleNamespace(type="message_stop")])
        list(provider.chat_stream([{"role": "system", "content": "alice@example.org"}, {"role": "user", "content": "alice@example.org"}]))
        params = client.messages.stream.call_args.kwargs
        self.assertIn("alice@example.org", str(params["system"]))
        self.assertNotIn("alice@example.org", str(params["messages"]))
        self.assertEqual(module.Anthropic.call_args.kwargs["api_key"], "real-auth-test")

    def test_worker_defers_entire_batch_and_regenerates_once_before_execution(self):
        import test_agent_bootstrap as bootstrap
        worker_ref = {}

        def ready(worker):
            worker.data_security_run.policy = enabled()
            worker.data_security_run.data_dir = worker.workspace_dir
            worker._security_compat = True
            worker_ref["worker"] = worker

        def opaque_round():
            run = worker_ref["worker"].data_security_run
            token = run.project_request({"messages": [{"role": "user", "content": "alice@example.org"}]}, "chat_completions")["messages"][0]["content"]
            yield {"type": "reasoning", "content": "read"}
            yield bootstrap.tool_call("read_file", "opaque", json.dumps({"path": token}))
            yield {**bootstrap.tool_call("read_file", "other", '{"path":"other.txt"}'), "index": 1}

        requests, result, events, calls, _, _ = bootstrap.TestAgentBootstrap().run_worker(
            [opaque_round, [bootstrap.tool_call("read_file", "real", '{"path":"alice@example.org"}')],
             [{"type": "content", "content": "done"}]],
            history=[{"role": "user", "content": "read alice@example.org"}],
            profile={**bootstrap.PROFILE, "bootstrap_plugin": ""}, on_worker_ready=ready)
        self.assertNotIn("error", result)
        self.assertEqual(calls, [("read_file", {"path": "alice@example.org"})])
        self.assertEqual(len(requests), 3)
        skipped = [m for m in result["generated_messages"] if m.get("role") == "tool" and "not_executed_original_context_required" in m.get("content", "")]
        self.assertEqual(len(skipped), 2)
        self.assertEqual(sum(e.get("event") == "original_context" for e in events), 1)
        self.assertTrue(worker_ref["worker"].data_security_run.closed)

    def test_display_projection_joins_stream_fragments_without_mutating_native_history(self):
        token = "[[CW:111111111111:email:1111111111111111]]"
        messages = [{"id": "u", "role": "user", "content": "start"},
                    {"id": "fragment", "role": "assistant", "content": "alice@example.org",
                     "meta": {"ui_visible_fragment": True, "ui_source_message_id": "a"}},
                    {"id": "a", "role": "assistant", "content": token,
                     "meta": {"security_display_content": "alice@example.org", "data_security_tokens": True}}]
        original = copy.deepcopy(messages)
        projected = project_visible_messages(messages)
        self.assertEqual(messages, original)
        assistants = [m for m in projected if m.get("role") == "assistant"]
        self.assertEqual(len(assistants), 1)
        self.assertEqual(assistants[0]["content"], "alice@example.org")

    def test_disabled_worker_keeps_invalid_tool_arguments_existing_fallback(self):
        import test_agent_bootstrap as bootstrap
        requests, result, _, calls, _, _ = bootstrap.TestAgentBootstrap().run_worker(
            [[bootstrap.tool_call("read_file", "invalid", '{invalid')], [{"type": "content", "content": "done"}]],
            history=[{"role": "user", "content": "read"}], profile={**bootstrap.PROFILE, "bootstrap_plugin": ""})
        self.assertNotIn("error", result)
        self.assertEqual(calls, [("read_file", {})])
        self.assertEqual(len(requests), 2)

    def test_subagent_inherits_frozen_policy_and_conversation_scope(self):
        import test_agent_bootstrap as bootstrap
        def ready(worker):
            worker.data_security_run.policy = enabled()
            with patch("core.agent.LLMWorker") as child:
                worker._create_subagent_worker([], worker.config_manager, worker.workspace_dir, "child", worker.conversation_id)
            self.assertEqual(child.call_args.kwargs["data_security_policy"], enabled())
            self.assertEqual(child.call_args.kwargs["conversation_id"], worker.conversation_id)
        bootstrap.TestAgentBootstrap().run_worker([[{"type": "content", "content": "done"}]],
            history=[{"role": "user", "content": "read"}], profile={**bootstrap.PROFILE, "bootstrap_plugin": ""}, on_worker_ready=ready)

    def test_skill_generation_uses_original_once_for_opaque_generated_code(self):
        from core.skill_generator import SkillGenerator
        config = {"api_key": "test", "data_security": enabled()}
        generator = SkillGenerator(config)
        client = MagicMock()
        token_reply = json.dumps({"code": "print('[[CW:unknown]]')"})
        client.chat.completions.create.side_effect = [SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=token_reply))]),
                                                     SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"code":"print(1)"}'))])]
        with patch("core.skill_generator.OpenAI", return_value=client), patch("core.env_utils.get_app_data_dir", return_value=self.temp.name):
            result = generator.refactor_code("# alice@example.org\nprint(1)")
        self.assertEqual(result, {"code": "print(1)"})
        calls = client.chat.completions.create.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertNotIn("alice@example.org", str(calls[0].kwargs["messages"][1]))
        self.assertIn("alice@example.org", str(calls[1].kwargs["messages"][1]))


if __name__ == "__main__":
    unittest.main()
