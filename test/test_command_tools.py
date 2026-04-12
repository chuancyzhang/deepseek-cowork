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

        lines = result.splitlines()
        self.assertIn(os.path.normpath("src\\app.py"), lines)
        self.assertNotIn(os.path.normpath("node_modules\\skip.py"), lines)
        self.assertNotIn(os.path.normpath("src\\notes.txt"), lines)

    def test_glob_uses_path_scope_and_does_not_search_content(self):
        os.makedirs(os.path.join(self.workspace_dir, "docs"), exist_ok=True)
        with open(os.path.join(self.workspace_dir, "docs", "needle-guide.md"), "w", encoding="utf-8") as f:
            f.write("title\n")
        with open(os.path.join(self.workspace_dir, "docs", "plain.txt"), "w", encoding="utf-8") as f:
            f.write("needle only appears in file content\n")

        result = self.module.glob(self.workspace_dir, pattern="*needle*", path="docs")

        lines = result.splitlines()
        self.assertEqual(lines, [os.path.normpath("docs\\needle-guide.md")])

    def test_grep_only_matches_file_content(self):
        os.makedirs(os.path.join(self.workspace_dir, "src"), exist_ok=True)
        with open(os.path.join(self.workspace_dir, "src", "actual.txt"), "w", encoding="utf-8") as f:
            f.write("line 1\nneedle appears here\n")
        with open(os.path.join(self.workspace_dir, "src", "needle-name.txt"), "w", encoding="utf-8") as f:
            f.write("line 1\nno keyword in content\n")

        result = self.module.grep(self.workspace_dir, pattern="needle", path="src")

        self.assertIn(os.path.normpath("src\\actual.txt") + ":2: needle appears here", result.splitlines())
        self.assertNotIn(os.path.normpath("src\\needle-name.txt") + ":1: line 1", result.splitlines())

    def test_bash_uses_command_tools_skill_id(self):
        with patch.object(self.module, "run_in_sandbox", return_value=_FakeProcess(stdout=b"ok\n", stderr=b"")) as run_mock:
            result = self.module.bash(self.workspace_dir, "echo ok")

        self.assertEqual(result, "ok\n")
        _, kwargs = run_mock.call_args
        self.assertEqual(kwargs["cwd"], self.workspace_dir)
        self.assertEqual(kwargs["skill_id"], "command-tools")
        self.assertEqual(kwargs["shell_kind"], "bash")


if __name__ == "__main__":
    unittest.main()
