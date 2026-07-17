import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_module():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "ai_skills", "browser-automation", "impl.py")
    spec = importlib.util.spec_from_file_location("browser_automation_impl_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BrowserAutomationUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_module()

    def test_rejects_shell_command_string(self):
        payload = json.loads(self.module.browser_skill_cli("snapshot --session abcd"))
        self.assertEqual(payload["status"], "incomplete")
        self.assertIn("JSON array", payload["error"]["message"])

    def test_rejects_unsupported_command(self):
        payload = json.loads(self.module.browser_skill_cli(["daemon", "start"]))
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
            payload = json.loads(
                self.module.browser_skill_cli(
                    ["snapshot", "--session", "abcd"],
                    timeout_seconds=33,
                    workspace_dir=os.getcwd(),
                )
            )
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(
            run.call_args.args[0],
            ["snapshot", "--session", "abcd"],
        )
        self.assertEqual(run.call_args.kwargs["timeout_seconds"], 33)

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
