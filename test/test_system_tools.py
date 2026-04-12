import importlib.util
import json
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_system_tools_module():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    module_path = os.path.join(repo_root, "skills", "system-tools", "impl.py")
    spec = importlib.util.spec_from_file_location("system_tools_impl_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSystemTools(unittest.TestCase):
    def setUp(self):
        self.module = _load_system_tools_module()

    def test_system_automate_rejects_removed_search_action(self):
        payload = self.module.system_automate([{"action": "search", "query": "needle"}], workspace_dir="D:\\code\\cowork")
        result = json.loads(payload)
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"][0]["action"], "search")
        self.assertIn("unsupported action search", result["results"][0]["result"])

    def test_system_automate_rejects_removed_bash_action(self):
        payload = self.module.system_automate([{"action": "bash", "command": "echo hi"}], workspace_dir="D:\\code\\cowork")
        result = json.loads(payload)
        self.assertFalse(result["ok"])
        self.assertEqual(result["results"][0]["action"], "bash")
        self.assertIn("unsupported action bash", result["results"][0]["result"])


if __name__ == "__main__":
    unittest.main()
