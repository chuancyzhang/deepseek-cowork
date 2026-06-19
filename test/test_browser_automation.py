import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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

    def test_rejects_invalid_session_mode_before_launch(self):
        payload = json.loads(self.module.browser_automate([{"action": "observe"}], session_mode="shared"))
        self.assertEqual(payload["status"], "incomplete")
        self.assertIn("session_mode", payload["error"]["message"])

    def test_rejects_invalid_session_id_before_launch(self):
        payload = json.loads(self.module.browser_automate([{"action": "observe"}], session_id="../escape"))
        self.assertEqual(payload["status"], "incomplete")
        self.assertIn("session_id", payload["error"]["message"])

    def test_file_url_must_stay_inside_workspace(self):
        with tempfile.TemporaryDirectory() as workspace:
            inside = os.path.join(workspace, "page.html")
            outside = os.path.abspath(os.path.join(workspace, "..", "outside.html"))
            self.assertEqual(self.module._validate_url(self.module.Path(inside).as_uri(), workspace), self.module.Path(inside).as_uri())
            with self.assertRaises(ValueError):
                self.module._validate_url(self.module.Path(outside).as_uri(), workspace)

    def test_existing_mode_requires_explicit_chrome_debugging(self):
        state = {"lock": self.module.threading.Lock(), "process": None, "profile_dir": "", "mode": "", "endpoint": ""}
        with patch.object(self.module, "_chrome_profile_candidates", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "chrome://inspect"):
                self.module._ensure_endpoint(state, "existing", playwright=None)

    def test_visit_and_screenshot_uses_isolated_session(self):
        with patch.object(self.module, "browser_automate", return_value='{"status":"completed"}') as automate:
            result = self.module.visit_and_screenshot("https://example.com", workspace_dir="C:\\workspace")
        self.assertEqual(json.loads(result)["status"], "completed")
        self.assertEqual(automate.call_args.kwargs["session_mode"], "isolated")
        self.assertEqual(automate.call_args.args[0][0]["action"], "goto")

    def test_explicit_exports_match_skill_surface(self):
        self.assertEqual(
            [item["name"] for item in self.module.TOOL_EXPORTS],
            ["browser_automate", "get_active_tab_info", "visit_and_screenshot"],
        )
        self.assertTrue(self.module.TOOL_EXPORTS[0]["destructive"])
        self.assertTrue(self.module.TOOL_EXPORTS[1]["read_only"])


if __name__ == "__main__":
    unittest.main()
