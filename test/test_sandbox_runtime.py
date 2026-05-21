import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import sandbox_runtime


class TestSandboxRuntime(unittest.TestCase):
    def tearDown(self):
        sandbox_runtime._RUNTIME_CACHE = None

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
            bash_root = os.path.join(temp_dir, "git")
            fake_runtime = {
                "root": runtime_root,
                "python": os.path.join(temp_dir, "python", "python.exe"),
                "pip": os.path.join(temp_dir, "python", "pip.exe"),
                "node": os.path.join(temp_dir, "node", "node.exe"),
                "npm": os.path.join(temp_dir, "node", "npm.cmd"),
                "npx": os.path.join(temp_dir, "node", "npx.cmd"),
                "bash": os.path.join(bash_root, "usr", "bin", "bash.exe"),
            }
            for path in fake_runtime.values():
                if path == runtime_root:
                    continue
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if os.path.splitext(path)[1]:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("")
            os.makedirs(os.path.join(bash_root, "bin"), exist_ok=True)
            os.makedirs(os.path.join(bash_root, "mingw64", "bin"), exist_ok=True)

            with patch("core.sandbox_runtime.ensure_sandbox_runtime", return_value=fake_runtime):
                env = sandbox_runtime.build_sandbox_env(workspace_dir=temp_dir, skill_id="demo-skill")

        self.assertIn(os.path.join("skills", "demo-skill", "python", "site-packages"), env["PYTHONPATH"])
        self.assertTrue(env["NODE_PATH"].endswith(os.path.join("skills", "demo-skill", "node", "node_modules")))
        self.assertEqual(env["COWORK_WORKSPACE_DIR"], os.path.abspath(temp_dir))
        path_entries = env["PATH"].split(os.pathsep)
        self.assertIn(os.path.join(bash_root, "bin"), path_entries)
        self.assertIn(os.path.join(bash_root, "usr", "bin"), path_entries)
        self.assertIn(os.path.join(bash_root, "mingw64", "bin"), path_entries)

    def test_resolve_bash_detects_windows_frozen_internal_git_bash_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bash_exe = os.path.join(temp_dir, "_internal", "git_bash_env", "bin", "bash.exe")
            os.makedirs(os.path.dirname(bash_exe), exist_ok=True)
            with open(bash_exe, "w", encoding="utf-8") as f:
                f.write("")

            with patch("core.sandbox_runtime.get_base_dir", return_value=temp_dir), \
                 patch("core.sandbox_runtime._copy_runtime_dir", side_effect=lambda source, _name: source), \
                 patch.object(sandbox_runtime.sys, "frozen", True, create=True):
                resolved, diagnostics = sandbox_runtime._resolve_bash()

        self.assertEqual(resolved, bash_exe)
        self.assertIn(bash_exe, diagnostics["searched_paths"])

    def test_resolve_bash_prefers_explicit_executable_over_runtime_dirs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            explicit_exe = os.path.join(temp_dir, "manual", "bash.exe")
            fallback_exe = os.path.join(temp_dir, "_internal", "git_bash_env", "bin", "bash.exe")
            for path in (explicit_exe, fallback_exe):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")

            env = {
                "COWORK_BASH_EXE": explicit_exe,
                "COWORK_BASH_DIR": os.path.join(temp_dir, "unused-runtime-dir"),
            }
            with patch.dict(os.environ, env, clear=False), \
                 patch("core.sandbox_runtime.get_base_dir", return_value=temp_dir), \
                 patch("core.sandbox_runtime._copy_runtime_dir", side_effect=lambda source, _name: source), \
                 patch.object(sandbox_runtime.sys, "frozen", True, create=True):
                resolved, diagnostics = sandbox_runtime._resolve_bash()

        self.assertEqual(resolved, explicit_exe)
        self.assertEqual(diagnostics["env_overrides"]["COWORK_BASH_EXE"], explicit_exe)
        self.assertIn(fallback_exe, diagnostics["searched_paths"])

    def test_validate_python_runtime_rejects_unusable_venv_redirector(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            python_exe = os.path.join(temp_dir, "python.exe")
            with open(python_exe, "w", encoding="utf-8") as f:
                f.write("")
            with open(os.path.join(temp_dir, "pyvenv.cfg"), "w", encoding="utf-8") as f:
                f.write("executable = C:\\missing\\python.exe\n")

            with patch("core.sandbox_runtime._probe_executable", return_value=("", "missing base interpreter")):
                resolved, diagnostics = sandbox_runtime._validate_python_runtime(python_exe)

        self.assertEqual(resolved, "")
        self.assertIn("external base interpreter", diagnostics["error"])
        self.assertEqual(diagnostics["pyvenv_cfg"]["executable"], "C:\\missing\\python.exe")


if __name__ == "__main__":
    unittest.main()
