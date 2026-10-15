import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from core.llm.providers import OpenAIProvider, MoonshotProvider
from core.tool_images import ToolImageError


class ViewImageChatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "example.png"
        Image.new("RGB", (16, 12), "red").save(self.source)
        spec = importlib.util.spec_from_file_location("view_image_test_impl", Path(__file__).resolve().parents[1] / "ai_skills/view-image/impl.py")
        self.impl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.impl)
        self.context = {"session_id": "chat-a", "tool_call_id": "image-call", "model_api_protocol": "chat_completions",
                        "model_supports_vision": True, "config_manager": SimpleNamespace(get_chat_history_dir=lambda: str(self.root / "history"))}

    def result(self):
        return self.impl.view_image("example.png", workspace_dir=str(self.root), _context=self.context)

    def provider(self, cls=OpenAIProvider, vision=True, protocol="chat_completions"):
        return cls("unused", "https://example.invalid/v1", "vision-model", supports_vision=vision, api_protocol=protocol)

    def test_chat_projection_batches_tool_results_and_preserves_history_and_prefix(self):
        result = self.result()
        self.assertTrue(result["ok"], result)
        # The tool contract stores a replayable snapshot, never a base64 ledger.
        part = result["content_parts"][0]
        Image.new("RGB", (16, 12), "blue").save(self.source)
        messages = [
            {"role": "system", "content": "stable prefix"},
            {"role": "user", "content": "Inspect"},
            {"role": "assistant", "tool_calls": [{"id": call, "type": "function", "function": {"name": "view_image", "arguments": "{}"}} for call in ("image-call", "other-call")]},
            {"role": "tool", "tool_call_id": "image-call", "content": "image ready", "content_parts": [part]},
            {"role": "tool", "tool_call_id": "other-call", "content": "other result"},
        ]
        original = copy.deepcopy(messages)
        for cls in (OpenAIProvider, MoonshotProvider):
            with self.subTest(provider=cls.__name__):
                provider = self.provider(cls)
                wire, _ = provider._prepare_chat_completions_messages(messages)
                self.assertEqual([m["role"] for m in wire], ["system", "user", "assistant", "tool", "tool", "user"])
                self.assertIn("image-call", wire[-1]["content"][0]["text"])
                self.assertTrue(wire[-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
                again, _ = provider._prepare_chat_completions_messages(messages + [{"role": "assistant", "content": "red image"}])
                self.assertEqual(again[:-1], wire)
                self.assertEqual(messages, original)
                stream = iter([{"choices": [{"delta": {"content": "Red image"}, "finish_reason": "stop"}]}])
                with patch.object(provider, "_create_chat_completion_stream", return_value=stream) as request:
                    list(provider.chat_stream(messages))
                request.assert_called_once()
                self.assertEqual(request.call_args.args[0]["messages"], wire)
        responses = self.provider(protocol="responses")._prepare_responses_input(messages)
        self.assertEqual(responses[-2]["type"], "function_call_output")
        self.assertEqual(responses[-2]["output"][1]["type"], "input_image")

    def test_nonvision_and_modified_snapshot_fail_explicitly(self):
        result = self.result()
        message = {"role": "tool", "tool_call_id": "image-call", "content": "ready", "content_parts": result["content_parts"]}
        with self.assertRaises(ToolImageError) as error:
            self.provider(vision=False)._prepare_chat_completions_messages([message])
        self.assertEqual(error.exception.code, "vision_not_supported")
        Path(result["content_parts"][0]["path"]).write_bytes(b"changed")
        with self.assertRaises(ToolImageError):
            self.provider()._prepare_chat_completions_messages([message])
        self.context["model_supports_vision"] = False
        self.assertFalse(self.result()["ok"])

    def test_outside_workspace_image_is_not_read(self):
        result = self.impl.view_image(str(self.source), workspace_dir=str(self.root / "other"), _context=self.context)
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
