import importlib.util
import json
import copy
import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from PIL import Image

from core.llm.providers import OpenAIProvider
from core.tool_images import tool_image_content_parts
from core.chat_storage import ChatStorage


def _load_module():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "ai_skills", "browser-automation", "impl.py")
    spec = importlib.util.spec_from_file_location("browser_automation_impl_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BrowserAutomationUnitTests(unittest.TestCase):
    def setUp(self):
        from browser_test_context import authorize_browser_fixture
        authorize_browser_fixture(self)
    @classmethod
    def setUpClass(cls):
        cls.module = _load_module()

    def test_rejects_shell_command_string(self):
        payload = self.module.browser_skill_cli("snapshot --session abcd")
        self.assertEqual(payload["status"], "incomplete")
        self.assertIn("JSON array", payload["error"]["message"])

    def test_rejects_unsupported_command(self):
        payload = self.module.browser_skill_cli(["daemon", "start"])
        self.assertEqual(payload["status"], "incomplete")
        self.assertIn("unsupported", payload["error"]["message"])

    def test_file_url_must_stay_inside_workspace(self):
        with tempfile.TemporaryDirectory() as workspace:
            inside = os.path.join(workspace, "page.html")
            outside = os.path.abspath(os.path.join(workspace, "..", "outside.html"))
            normalized = self.module._normalize_args(
                ["navigate", Path(inside).as_uri()],
                workspace,
            )
            self.assertEqual(normalized[0], "navigate")
            with self.assertRaisesRegex(ValueError, "file URL"):
                self.module._normalize_args(
                    ["navigate", Path(outside).as_uri()],
                    workspace,
                )

    def test_screenshot_output_must_stay_inside_allowed_roots(self):
        with tempfile.TemporaryDirectory() as workspace:
            inside = os.path.join(workspace, "images", "page.png")
            normalized = self.module._normalize_args(
                ["screenshot", "--out", inside, "--session", "abcd"],
                workspace,
            )
            self.assertEqual(normalized[2], os.path.abspath(inside))
            outside = os.path.abspath(os.path.join(workspace, "..", "outside.png"))
            with self.assertRaisesRegex(ValueError, "screenshot output"):
                self.module._normalize_args(
                    ["screenshot", "--out", outside, "--session", "abcd"],
                    workspace,
                )

    def test_sensitive_evaluate_is_rejected(self):
        for expression in (
            "document.cookie",
            "localStorage.getItem('token')",
            "sessionStorage.clear()",
            "fetch('/api', {headers: {Authorization: 'x'}})",
        ):
            with self.subTest(expression=expression):
                with self.assertRaisesRegex(ValueError, "cannot read"):
                    self.module._normalize_args(["evaluate", expression], os.getcwd())

    def test_cli_uses_structured_adapter(self):
        completed = {"status": "completed", "result": {"ok": True}}
        with patch.object(self.module, "run_browser_skill_cli", return_value=completed) as run:
            payload = self.module.browser_skill_cli(
                ["snapshot", "--session", "abcd"],
                timeout_seconds=33,
                workspace_dir=os.getcwd(),
            )
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(
            run.call_args.args[0],
            ["snapshot", "--session", "abcd"],
        )
        self.assertEqual(run.call_args.kwargs["timeout_seconds"], 33)

    def test_observe_and_command_help_reach_the_managed_cli(self):
        for args in (["observe", "--session", "abcd", "--tab-id", "42"],
                     ["observe", "--session", "abcd", "--cursor", "next"],
                     ["observe", "--help"], ["snapshot", "--help"]):
            with self.subTest(args=args), patch.object(self.module, "run_browser_skill_cli", return_value={
                "status": "completed", "result": {"text": "current page"},
            }) as run:
                result = self.module.browser_skill_cli(args)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(run.call_args.args[0], args)


class BrowserScreenshotTests(unittest.TestCase):
    def setUp(self):
        from browser_test_context import authorize_browser_fixture
        self.authorization = authorize_browser_fixture(self)
        self.module = _load_module()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.context = {
            "session_id": "chat-a", "tool_call_id": "screenshot-call",
            "model_api_protocol": "responses", "model_supports_vision": True,
            "config_manager": SimpleNamespace(get_chat_history_dir=lambda: str(self.root / "history")),
        }
        self.output = None
        self.cli_result = {"status": "completed", "result": {"ok": True}, "error": None}
        appdata = patch.object(self.module, "get_app_data_dir", return_value=str(self.root / "appdata"))
        appdata.start()
        self.addCleanup(appdata.stop)
        logger = patch.object(self.module, "log_browser_skill_event")
        self.events = logger.start()
        self.addCleanup(logger.stop)

    def capture(self, args, **kwargs):
        self.output = Path(args[args.index("--out") + 1])
        Image.new("RGB", (32, 24), "red").save(self.output)
        return copy.deepcopy(self.cli_result)

    def call(self, args=None, **kwargs):
        return self.module.browser_skill_cli(
            args or ["screenshot", "--session", "abcd"],
            workspace_dir=str(self.workspace), _context=self.context, **kwargs,
        )

    def test_screenshot_image_reaches_both_protocols_and_replays_original_bytes(self):
        for protocol in ("responses", "chat_completions"):
            with self.subTest(protocol=protocol), patch.object(self.module, "run_browser_skill_cli", side_effect=self.capture):
                self.context["model_api_protocol"] = protocol
                result = self.call()
                self.assertEqual(result["status"], "completed", result)
                parts = tool_image_content_parts(result)
                self.assertEqual(len(parts), 1)
                original = self.output.read_bytes()
                self.output.unlink()
                messages = [
                    {"role": "system", "content": "stable prefix"},
                    {"role": "assistant", "tool_calls": [{"id": "screenshot-call", "type": "function", "function": {"name": "browser_skill_cli", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "screenshot-call", "content": json.dumps(result), "content_parts": parts},
                ]
                storage = ChatStorage(str(self.root / (protocol + ".sqlite")))
                storage.save_conversation_safely("chat-a", messages)
                messages = storage.get_messages("chat-a")
                self.assertEqual(messages[-1]["content_parts"], parts)
                provider = OpenAIProvider("unused", "https://example.invalid/v1", "vision-model", supports_vision=True, api_protocol=protocol)
                before = copy.deepcopy(messages)
                if protocol == "responses":
                    wire = provider._prepare_responses_input(messages)
                    url = wire[-1]["output"][1]["image_url"]
                    again = provider._prepare_responses_input(messages + [{"role": "assistant", "content": "red"}])
                else:
                    wire, _ = provider._prepare_chat_completions_messages(messages)
                    url = wire[-1]["content"][1]["image_url"]["url"]
                    again, _ = provider._prepare_chat_completions_messages(messages + [{"role": "assistant", "content": "red"}])
                self.assertEqual(base64.b64decode(url.split(",", 1)[1]), original)
                self.assertEqual(again[:-1], wire)
                self.assertEqual(messages, before)
                self.assertNotIn("base64", json.dumps(result))

    def test_actual_agent_loop_receives_native_browser_image(self):
        from test_agent_bootstrap import TestAgentBootstrap, SkillStub, ProviderStub, tool_call
        from core.llm.providers import OpenAIProvider

        owner = self
        original_stream = ProviderStub.chat_stream
        projections = []

        def definitions(_skills, **kwargs):
            export = owner.module.TOOL_EXPORTS[0]
            return [{"type": "function", "function": {
                key: export[key] for key in ("name", "description", "parameters")}}]

        def execute(skills, name, args, context=None):
            skills.calls.append((name, copy.deepcopy(args)))
            # This test exercises the image pipeline after an owned session exists.
            from core.execution_authorization import current_authorization
            auth = current_authorization(context)
            coordinator = auth._browser_state["coordinator"]
            coordinator.acquire(auth.session_id, auth.run_id, auth._browser_state["snapshot"], "external")
            coordinator.track_session(auth.session_id, auth.run_id, "abcd")
            coordinator.complete_call(auth.session_id, auth.run_id)
            return owner.module.browser_skill_cli(**args, workspace_dir=str(owner.workspace), _context=context)

        def stream(provider, messages, **kwargs):
            if any(message.get("role") == "tool" for message in messages):
                encoder = OpenAIProvider("unused", "https://example.invalid/v1", "vision-model", supports_vision=True, api_protocol="chat_completions")
                projections.append(encoder._prepare_chat_completions_messages(messages)[0])
            yield from original_stream(provider, messages, **kwargs)

        with patch.object(SkillStub, "get_tool_definitions", definitions), \
                patch.object(SkillStub, "call_tool", execute), \
                patch.object(ProviderStub, "supports_vision", True, create=True), \
                patch.object(ProviderStub, "chat_stream", stream), \
                patch("core.browser_skill_component._stop_session_after_interruption", return_value={"stopped": True}), \
                patch.object(self.module, "run_browser_skill_cli", side_effect=self.capture):
            requests, result, events, calls, _, _ = TestAgentBootstrap().run_worker(
                [[tool_call("browser_skill_cli", arguments=json.dumps({"args": ["screenshot", "--session", "abcd"]}))],
                 [{"type": "content", "content": "Screenshot inspected."}]],
                profile={"provider_type": "openai", "model_name": "vision-model", "base_url": "https://example.invalid/v1"})
        self.assertNotIn("error", result)
        self.assertEqual(len(calls), 1)
        message = next(message for message in requests[-1]["messages"] if message.get("role") == "tool")
        self.assertEqual(message["result_obj"]["image_status"], "ready")
        self.assertEqual(len(message["content_parts"]), 1)
        self.assertTrue(any(part.get("type") == "image_url" for message in projections[-1]
                            for part in (message.get("content") if isinstance(message.get("content"), list) else [])))

    def test_save_only_works_without_model_or_session_context(self):
        self.context.clear()
        with patch.object(self.module, "run_browser_skill_cli", side_effect=self.capture):
            result = self.call(["screenshot", "--session", "abcd", "--out=images/page.png"], include_image=False)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["image_status"], "not_requested")
        self.assertEqual(self.output, self.workspace / "images/page.png")
        self.assertTrue(self.output.exists())
        self.assertNotIn("content_parts", result)

    def test_nonvision_unsupported_protocol_and_missing_context_retain_capture(self):
        for change, code in (({"model_supports_vision": False}, "vision_not_supported"),
                             ({"model_api_protocol": "unsupported"}, "unsupported_protocol"),
                             ({"session_id": ""}, "session_unavailable")):
            with self.subTest(code=code), patch.dict(self.context, change), patch.object(self.module, "run_browser_skill_cli", side_effect=self.capture):
                result = self.call()
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(result["screenshot_status"], "completed")
                self.assertEqual(result["error"]["code"], code)
                self.assertTrue(self.output.exists())
                self.assertEqual(result["result"], self.cli_result["result"])
                self.assertNotIn("content_parts", result)

    def test_persistence_failure_preserves_file_and_original_result(self):
        with patch.object(self.module, "run_browser_skill_cli", side_effect=self.capture), patch.object(self.module, "persist_tool_image", side_effect=OSError("disk full")):
            result = self.call()
        self.assertEqual(result["image_status"], "failed")
        self.assertTrue(self.output.exists())
        self.assertEqual(result["artifacts"][0]["path"], str(self.output))
        self.assertEqual(result["result"], self.cli_result["result"])

    def test_cli_failure_does_not_attach_any_image(self):
        failed = {"status": "incomplete", "error": {"code": "bsk_command_failed"}}
        with patch.object(self.module, "run_browser_skill_cli", return_value=failed), patch.object(self.module, "persist_tool_image") as persist:
            self.assertEqual(self.call(), failed)
        persist.assert_not_called()

    def test_cancel_after_capture_preserves_file_without_image_delivery(self):
        cancelled = [False]
        self.context["abort_check"] = lambda: cancelled[0]
        def capture_and_cancel(*args, **kwargs):
            result = self.capture(*args, **kwargs)
            cancelled[0] = True
            return result
        with patch.object(self.module, "run_browser_skill_cli", side_effect=capture_and_cancel):
            result = self.call()
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(self.output.exists())
        self.assertNotIn("content_parts", result)

    def test_conversations_have_separate_snapshots_and_generated_paths(self):
        with patch.object(self.module, "run_browser_skill_cli", side_effect=self.capture):
            first = self.call()
            self.context["session_id"] = "chat-b"
            second = self.call()
        self.assertNotEqual(first["content_parts"][0]["path"], second["content_parts"][0]["path"])
        self.assertNotEqual(first["artifacts"][0]["path"], second["artifacts"][0]["path"])
        self.assertEqual(first["content_parts"][0]["sha256"], second["content_parts"][0]["sha256"])

    def test_missing_or_invalid_image_cannot_be_reported_as_seen(self):
        def invalid_capture(args, **kwargs):
            self.output = Path(args[args.index("--out") + 1])
            self.output.write_bytes(b"invalid PNG")
            return copy.deepcopy(self.cli_result)
        for capture, code in ((lambda *args, **kwargs: copy.deepcopy(self.cli_result), "not_a_file"), (invalid_capture, "invalid_image")):
            with self.subTest(code=code), patch.object(self.module, "run_browser_skill_cli", side_effect=capture):
                result = self.call()
                self.assertEqual(result["error"]["code"], code)
                self.assertNotIn("content_parts", result)

    def test_rejects_outside_and_duplicate_output_before_cli(self):
        for extra in (("--out=" + str(self.root / "outside.png"),),
                      ("--out", "one.png", "--out=two.png"), ("--out=",)):
            with self.subTest(args=extra), patch.object(self.module, "run_browser_skill_cli") as run:
                result = self.call(["screenshot", "--session", "abcd", *extra])
                self.assertEqual(result["status"], "incomplete")
                run.assert_not_called()

    def test_explicit_export_matches_new_skill_surface(self):
        self.assertEqual(
            [item["name"] for item in self.module.TOOL_EXPORTS],
            ["browser_skill_cli"],
        )
        self.assertTrue(self.module.TOOL_EXPORTS[0]["destructive"])
        self.assertEqual(
            self.module.TOOL_EXPORTS[0]["metadata"]["component_id"],
            "browser-skill",
        )


if __name__ == "__main__":
    unittest.main()
