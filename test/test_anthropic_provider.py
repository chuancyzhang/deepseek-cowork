import os
import sys
import unittest
from types import ModuleType
from unittest.mock import MagicMock, patch


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm.providers import AnthropicProvider


class TestAnthropicProviderMessages(unittest.TestCase):
    def _build_provider(self):
        provider = AnthropicProvider.__new__(AnthropicProvider)
        provider.supports_vision = False
        return provider

    def test_anthropic_stream_read_timeout_is_sixty_seconds(self):
        module = ModuleType("anthropic")
        module.Anthropic = MagicMock()
        with patch.dict(sys.modules, {"anthropic": module}):
            AnthropicProvider("key", "https://example.test", "model")
        self.assertEqual(module.Anthropic.call_args.kwargs["timeout"].read, 60.0)

    @staticmethod
    def _tool_call(call_id, name):
        return {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": "{}"},
        }

    def test_merges_multiple_tool_results_into_one_user_message(self):
        provider = self._build_provider()

        _system, prepared = provider._prepare_messages([
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    self._tool_call("call-1", "glob"),
                    self._tool_call("call-2", "run_python_code"),
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "files"},
            {"role": "tool", "tool_call_id": "call-2", "content": "ok"},
        ])

        self.assertEqual(len(prepared), 2)
        self.assertEqual(prepared[1], {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call-1", "content": "files"},
                {"type": "tool_result", "tool_use_id": "call-2", "content": "ok"},
            ],
        })

    def test_keeps_separate_tool_rounds_separate(self):
        provider = self._build_provider()

        _system, prepared = provider._prepare_messages([
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [self._tool_call("call-1", "glob")],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "files"},
            {"role": "assistant", "content": "finished"},
            {"role": "user", "content": "check again"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [self._tool_call("call-2", "run_python_code")],
            },
            {"role": "tool", "tool_call_id": "call-2", "content": "ok"},
        ])

        result_messages = [
            message for message in prepared
            if isinstance(message.get("content"), list)
            and message["content"]
            and message["content"][0].get("type") == "tool_result"
        ]
        self.assertEqual(len(result_messages), 2)
        self.assertEqual(result_messages[0]["content"][0]["tool_use_id"], "call-1")
        self.assertEqual(result_messages[1]["content"][0]["tool_use_id"], "call-2")

    def test_rejects_tool_result_without_call_id(self):
        provider = self._build_provider()

        with self.assertRaisesRegex(ValueError, "missing tool_call_id"):
            provider._prepare_messages([
                {"role": "tool", "content": "orphan result"},
            ])


if __name__ == "__main__":
    unittest.main()
