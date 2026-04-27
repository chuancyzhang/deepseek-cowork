import os
import sys
import unittest
from types import ModuleType
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.agent import clear_reasoning_content, repair_tool_call_sequence, sanitize_llm_messages
from core.llm.providers import OpenAIProvider


class TestOpenAIProviderDeepSeek(unittest.TestCase):
    def _build_provider(self, **kwargs):
        client = MagicMock()
        openai_module = ModuleType("openai")
        openai_module.OpenAI = MagicMock(return_value=client)
        patcher = patch.dict(sys.modules, {"openai": openai_module})
        mock_openai = patcher.start()
        self.addCleanup(patcher.stop)
        provider = OpenAIProvider(
            api_key="test-key",
            base_url=kwargs.get("base_url", "https://api.deepseek.com"),
            model_name=kwargs.get("model_name", "deepseek-v4-pro"),
            thinking_enabled=kwargs.get("thinking_enabled", True),
            reasoning_effort=kwargs.get("reasoning_effort", "high"),
        )
        self.assertIn("openai", sys.modules)
        return provider, client

    def test_deepseek_requests_include_thinking_and_reasoning_effort(self):
        provider, client = self._build_provider(reasoning_effort="max")
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return []

        client.chat.completions.create.side_effect = create
        list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        self.assertEqual(captured["reasoning_effort"], "max")
        self.assertEqual(captured["extra_body"]["thinking"]["type"], "enabled")

    def test_non_deepseek_requests_skip_deepseek_only_options(self):
        provider, client = self._build_provider(
            base_url="https://api.openai.com/v1",
            model_name="gpt-4.1-mini",
            thinking_enabled=False,
            reasoning_effort="max",
        )
        captured = {}

        def create(**kwargs):
            captured.update(kwargs)
            return []

        client.chat.completions.create.side_effect = create
        list(provider.chat_stream([{"role": "user", "content": "hello"}]))

        self.assertNotIn("reasoning_effort", captured)
        self.assertNotIn("extra_body", captured)

    def test_prepare_messages_keeps_reasoning_for_tool_call_turns(self):
        provider, _client = self._build_provider()
        prepared = provider._prepare_messages([
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "keep me",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": "{}"},
                    }
                ],
            }
        ])

        self.assertIsNone(prepared[0]["content"])
        self.assertEqual(prepared[0]["reasoning_content"], "keep me")


class TestDeepSeekMessageSanitization(unittest.TestCase):
    def test_clear_reasoning_content_preserves_tool_call_turns_only(self):
        cleaned = clear_reasoning_content([
            {"role": "assistant", "content": "plain", "reasoning_content": "drop"},
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "keep",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": "{}"},
                    }
                ],
            },
        ])

        self.assertNotIn("reasoning_content", cleaned[0])
        self.assertEqual(cleaned[1]["reasoning_content"], "keep")

    def test_sanitize_llm_messages_preserves_reasoning_for_tool_turns(self):
        sanitized = sanitize_llm_messages([
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "keep",
                "reasoning": "ui-only",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            {"role": "assistant", "content": "final", "reasoning_content": "drop"},
        ])

        self.assertEqual(sanitized[0]["reasoning_content"], "keep")
        self.assertNotIn("reasoning", sanitized[0])
        self.assertNotIn("reasoning_content", sanitized[2])

    def test_repair_tool_call_sequence_keeps_valid_assistant_tool_roundtrip(self):
        repaired = repair_tool_call_sequence([
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "keep",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "demo", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ])

        self.assertEqual(len(repaired), 3)
        self.assertEqual(repaired[0]["tool_calls"][0]["id"], "call-1")
        self.assertEqual(repaired[1]["tool_call_id"], "call-1")


if __name__ == "__main__":
    unittest.main()
