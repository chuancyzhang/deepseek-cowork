import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import sandbox_runtime


class TestSandboxRuntime(unittest.TestCase):
    def test_build_skill_script_command_covers_python_node_and_bash(self):
        with patch("core.sandbox_runtime.get_runtime_executable") as get_exec:
            get_exec.side_effect = lambda name: {
                "python": os.path.normpath("C:\\runtime\\python.exe"),
                "node": os.path.normpath("C:\\runtime\\node.exe"),
                "bash": os.path.normpath("C:\\runtime\\bash.exe"),
            }.get(name, "")

            py_cmd, py_kind = sandbox_runtime.build_skill_script_command("python", "scripts/hello.py", args=["--x"])
            node_cmd, node_kind = sandbox_runtime.build_skill_script_command("node", "scripts/hello.js", args=["--y"])
            bash_cmd, bash_kind = sandbox_runtime.build_skill_script_command("bash", "scripts/hello.sh", args=["--z"])

        self.assertEqual(py_kind, "exec")
        self.assertEqual(py_cmd[:3], [os.path.normpath("C:\\runtime\\python.exe"), "-X", "utf8"])
        self.assertTrue(py_cmd[3].endswith(os.path.normpath("scripts\\hello.py")))
        self.assertEqual(py_cmd[4:], ["--x"])

        self.assertEqual(node_kind, "exec")
        self.assertEqual(node_cmd[0], os.path.normpath("C:\\runtime\\node.exe"))
        self.assertTrue(node_cmd[1].endswith(os.path.normpath("scripts\\hello.js")))
        self.assertEqual(node_cmd[2:], ["--y"])

        self.assertEqual(bash_kind, "exec")
        self.assertEqual(bash_cmd[0], os.path.normpath("C:\\runtime\\bash.exe"))
        self.assertTrue(bash_cmd[1].endswith(os.path.normpath("scripts\\hello.sh")))
        self.assertEqual(bash_cmd[2:], ["--z"])

    def test_build_sandbox_env_injects_skill_specific_python_and_node_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = os.path.join(temp_dir, "sandbox")
            fake_runtime = {
                "root": runtime_root,
                "python": os.path.join(temp_dir, "python", "python.exe"),
                "pip": os.path.join(temp_dir, "python", "pip.exe"),
                "node": os.path.join(temp_dir, "node", "node.exe"),
                "npm": os.path.join(temp_dir, "node", "npm.cmd"),
                "npx": os.path.join(temp_dir, "node", "npx.cmd"),
                "bash": os.path.join(temp_dir, "git", "bin", "bash.exe"),
            }
            for path in fake_runtime.values():
                if path == runtime_root:
                    continue
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if os.path.splitext(path)[1]:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("")

            with patch("core.sandbox_runtime.ensure_sandbox_runtime", return_value=fake_runtime):
                env = sandbox_runtime.build_sandbox_env(workspace_dir=temp_dir, skill_id="demo-skill")

        self.assertIn(os.path.join("skills", "demo-skill", "python", "site-packages"), env["PYTHONPATH"])
        self.assertTrue(env["NODE_PATH"].endswith(os.path.join("skills", "demo-skill", "node", "node_modules")))
        self.assertEqual(env["COWORK_WORKSPACE_DIR"], os.path.abspath(temp_dir))


if __name__ == "__main__":
    unittest.main()
