import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_command_tools_module():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    module_path = os.path.join(repo_root, "skills", "command-tools", "impl.py")
    spec = importlib.util.spec_from_file_location("command_tools_impl_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeProcess:
    def __init__(self, stdout=b"", stderr=b""):
        self._stdout = stdout
        self._stderr = stderr

    def communicate(self, timeout=None):
        return self._stdout, self._stderr


class TestCommandTools(unittest.TestCase):
    def setUp(self):
        self.workspace_dir = tempfile.mkdtemp()
        self.module = _load_command_tools_module()

    def tearDown(self):
        shutil.rmtree(self.workspace_dir, ignore_errors=True)

    def test_glob_returns_relative_workspace_matches(self):
        os.makedirs(os.path.join(self.workspace_dir, "src"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_dir, "node_modules"), exist_ok=True)
        with open(os.path.join(self.workspace_dir, "src", "app.py"), "w", encoding="utf-8") as f:
            f.write("print('ok')\n")
        with open(os.path.join(self.workspace_dir, "src", "notes.txt"), "w", encoding="utf-8") as f:
            f.write("app.py appears only in content\n")
        with open(os.path.join(self.workspace_dir, "node_modules", "skip.py"), "w", encoding="utf-8") as f:
            f.write("print('skip')\n")

        result = self.module.glob(self.workspace_dir, pattern="*.py")

        self.assertTrue(result["ok"])
        self.assertIn("src/app.py", result["items"])
        self.assertNotIn("node_modules/skip.py", result["items"])
        self.assertNotIn("src/notes.txt", result["items"])

    def test_glob_uses_path_scope_and_does_not_search_content(self):
        os.makedirs(os.path.join(self.workspace_dir, "docs"), exist_ok=True)
        with open(os.path.join(self.workspace_dir, "docs", "needle-guide.md"), "w", encoding="utf-8") as f:
            f.write("title\n")
        with open(os.path.join(self.workspace_dir, "docs", "plain.txt"), "w", encoding="utf-8") as f:
            f.write("needle only appears in file content\n")

        result = self.module.glob(self.workspace_dir, pattern="*needle*", path="docs")

        self.assertTrue(result["ok"])
        self.assertEqual(result["items"], ["docs/needle-guide.md"])

    def test_grep_only_matches_file_content(self):
        os.makedirs(os.path.join(self.workspace_dir, "src"), exist_ok=True)
        with open(os.path.join(self.workspace_dir, "src", "actual.txt"), "w", encoding="utf-8") as f:
            f.write("line 1\nneedle appears here\n")
        with open(os.path.join(self.workspace_dir, "src", "needle-name.txt"), "w", encoding="utf-8") as f:
            f.write("line 1\nno keyword in content\n")

        result = self.module.grep(self.workspace_dir, pattern="needle", path="src")

        self.assertTrue(result["ok"])
        self.assertIn(
            {"path": "src/actual.txt", "line": 2, "text": "needle appears here"},
            result["matches"],
        )
        self.assertFalse(any(item["path"] == "src/needle-name.txt" for item in result["matches"]))

    def test_grep_reports_strict_decode_warning(self):
        with open(os.path.join(self.workspace_dir, "legacy.txt"), "wb") as handle:
            handle.write("中文".encode("gbk"))

        result = self.module.grep(self.workspace_dir, pattern="中文")

        self.assertTrue(result["ok"])
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(result["warnings"][0]["code"], "encoding_required")

    def test_bash_uses_command_tools_skill_id(self):
        with patch.object(self.module, "run_in_sandbox", return_value=_FakeProcess(stdout=b"ok\n", stderr=b"")) as run_mock:
            result = self.module.bash(self.workspace_dir, "echo ok")

        self.assertEqual(result, "ok\n")
        _, kwargs = run_mock.call_args
        self.assertEqual(kwargs["cwd"], self.workspace_dir)
        self.assertEqual(kwargs["skill_id"], "command-tools")
        self.assertEqual(kwargs["shell_kind"], "bash")

    def test_run_node_code_uses_node_runtime_and_workspace(self):
        node_exe = os.path.normpath("C:\\runtime\\node.exe")
        with patch.object(self.module, "get_runtime_executable", return_value=node_exe), \
             patch.object(self.module, "run_in_sandbox", return_value=_FakeProcess(stdout=b"node ok\n", stderr=b"")) as run_mock:
            result = self.module.run_node_code(self.workspace_dir, "console.log('node ok')")

        self.assertEqual(result, "node ok\n")
        args, kwargs = run_mock.call_args
        command = args[0]
        self.assertEqual(command[0], node_exe)
        self.assertTrue(command[1].endswith(".js"))
        self.assertEqual(kwargs["cwd"], self.workspace_dir)
        self.assertEqual(kwargs["skill_id"], "command-tools")
        self.assertEqual(kwargs["shell_kind"], "exec")

    def test_run_node_code_reports_missing_node_runtime(self):
        with patch.object(self.module, "get_runtime_executable", return_value=""), \
             patch.object(self.module, "ask_user", return_value=False):
            result = self.module.run_node_code(self.workspace_dir, "console.log('x')")

        self.assertIn("Node.js runtime is not installed", result)

    def test_run_node_code_is_exported(self):
        export_names = {item["name"] for item in self.module.TOOL_EXPORTS}

        self.assertIn("run_node_code", export_names)


if __name__ == "__main__":
    unittest.main()
