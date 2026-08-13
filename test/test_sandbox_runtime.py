import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import sandbox_runtime


class TestSandboxRuntime(unittest.TestCase):
    def test_skill_script_abort_terminates_process_tree(self):
        process = MagicMock()
        process.poll.return_value = None
        command = ["python", "demo.py"]
        with patch.object(
            sandbox_runtime,
            "build_skill_script_command",
            return_value=(command, "exec"),
        ), patch.object(
            sandbox_runtime,
            "run_in_sandbox",
            return_value=process,
        ), patch.object(
            sandbox_runtime,
            "terminate_process_tree",
        ) as terminate:
            result = sandbox_runtime.run_skill_script_in_sandbox(
                "demo",
                "demo.py",
                "python",
                abort_check=lambda: True,
            )

        self.assertTrue(result["aborted"])
        self.assertFalse(result["ok"])
        terminate.assert_called_once_with(process)

    def tearDown(self):
        sandbox_runtime._RUNTIME_CACHE = None
        sandbox_runtime.reset_native_library_dir_caches()

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
            skill_site_packages = os.path.join(temp_dir, "runtime_sandbox", "v1", "skills", "demo-skill", "python", "site-packages")
            os.makedirs(skill_site_packages, exist_ok=True)
            with open(os.path.join(skill_site_packages, "demo_native.dll"), "w", encoding="utf-8") as f:
                f.write("")

            with patch("core.sandbox_runtime.ensure_sandbox_runtime", return_value=fake_runtime), \
                 patch("core.sandbox_runtime.get_app_data_dir", return_value=temp_dir):
                env = sandbox_runtime.build_sandbox_env(workspace_dir=temp_dir, skill_id="demo-skill")
                bootstrap_dir = os.path.join(runtime_root, "python_bootstrap")
                self.assertIn(os.path.join("skills", "demo-skill", "python", "site-packages"), env["PYTHONPATH"])
                self.assertIn(bootstrap_dir, env["PYTHONPATH"])
                self.assertTrue(env["NODE_PATH"].endswith(os.path.join("skills", "demo-skill", "node", "node_modules")))
                self.assertEqual(env["COWORK_WORKSPACE_DIR"], os.path.abspath(temp_dir))
                path_entries = env["PATH"].split(os.pathsep)
                self.assertIn(os.path.join(bash_root, "bin"), path_entries)
                self.assertIn(os.path.join(bash_root, "usr", "bin"), path_entries)
                self.assertIn(os.path.join(bash_root, "mingw64", "bin"), path_entries)
                self.assertIn(skill_site_packages, env["COWORK_PYTHON_DLL_DIRS"].split(os.pathsep))
                self.assertTrue(os.path.isfile(os.path.join(bootstrap_dir, "sitecustomize.py")))

    def test_python_dll_dirs_reuses_base_scan_across_skills(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            python_root = os.path.join(temp_dir, "python")
            runtime_site_packages = os.path.join(python_root, "Lib", "site-packages")
            os.makedirs(runtime_site_packages, exist_ok=True)
            with open(os.path.join(runtime_site_packages, "runtime_native.pyd"), "w", encoding="utf-8") as f:
                f.write("")

            calls = []
            original_collect = sandbox_runtime._collect_native_library_dirs

            def wrapped_collect(*roots):
                calls.append(tuple(root for root in roots if root))
                return original_collect(*roots)

            fake_runtime = {"python": os.path.join(python_root, "python.exe")}
            with patch("core.sandbox_runtime.get_app_data_dir", return_value=temp_dir), \
                 patch("core.sandbox_runtime._toolkit_python_paths", return_value=[]), \
                 patch("core.sandbox_runtime._collect_native_library_dirs", side_effect=wrapped_collect):
                first = sandbox_runtime._python_dll_dirs(fake_runtime, skill_id="first")
                second = sandbox_runtime._python_dll_dirs(fake_runtime, skill_id="second")

        self.assertIn(runtime_site_packages, first)
        self.assertIn(runtime_site_packages, second)
        self.assertEqual(len(calls), 3)
        self.assertIn(runtime_site_packages, calls[0])
        self.assertTrue(calls[1][0].endswith(os.path.join("skills", "first", "python", "site-packages")))
        self.assertTrue(calls[2][0].endswith(os.path.join("skills", "second", "python", "site-packages")))

    def test_install_skill_dependencies_clears_skill_dll_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = os.path.join(temp_dir, "sandbox")
            python_exe = os.path.join(temp_dir, "python", "python.exe")
            os.makedirs(os.path.dirname(python_exe), exist_ok=True)
            with open(python_exe, "w", encoding="utf-8") as f:
                f.write("")

            fake_runtime = {
                "root": runtime_root,
                "python": python_exe,
                "pip": python_exe,
                "node": "",
                "npm": "",
                "npx": "",
                "bash": "",
            }

            with patch("core.sandbox_runtime.ensure_sandbox_runtime", return_value=fake_runtime), \
                 patch("core.sandbox_runtime.get_app_data_dir", return_value=temp_dir), \
                 patch("core.sandbox_runtime.subprocess.check_output", return_value="installed"):
                sandbox_runtime._python_dll_dirs(fake_runtime, skill_id="demo-skill")
                self.assertIn("demo-skill", sandbox_runtime._SKILL_NATIVE_LIBRARY_DIRS_CACHE)
                result = sandbox_runtime.install_skill_dependencies("demo-skill", python_dependencies=["Pillow"], force=True)

        self.assertTrue(result["ok"])
        self.assertNotIn("demo-skill", sandbox_runtime._SKILL_NATIVE_LIBRARY_DIRS_CACHE)

    def test_resolve_bash_detects_windows_frozen_internal_git_bash_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bash_exe = os.path.join(temp_dir, "_internal", "git_bash_env", "bin", "bash.exe")
            os.makedirs(os.path.dirname(bash_exe), exist_ok=True)
            with open(bash_exe, "w", encoding="utf-8") as f:
                f.write("")

            with patch("core.sandbox_runtime.get_base_dir", return_value=temp_dir), \
                 patch("core.sandbox_runtime.get_app_data_dir", return_value=os.path.join(temp_dir, "data")), \
                 patch("core.sandbox_runtime._copy_runtime_dir", side_effect=lambda source, _name: source), \
                 patch.object(sandbox_runtime.sys, "frozen", True, create=True):
                resolved, diagnostics = sandbox_runtime._resolve_bash()

        self.assertEqual(resolved, bash_exe)
        self.assertIn(bash_exe, diagnostics["searched_paths"])

    def test_frozen_runtime_resolution_prefers_internal_runtime_layout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_node = os.path.join(temp_dir, "node_env", "node.exe")
            internal_node = os.path.join(temp_dir, "_internal", "node_env", "node.exe")
            for path in (legacy_node, internal_node):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")

            with patch("core.sandbox_runtime.get_base_dir", return_value=temp_dir), \
                 patch("core.sandbox_runtime.get_app_data_dir", return_value=os.path.join(temp_dir, "data")), \
                 patch.object(sandbox_runtime.sys, "frozen", True, create=True):
                resolved = sandbox_runtime._resolve_node()

        self.assertEqual(resolved, internal_node)

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
                 patch("core.sandbox_runtime.get_app_data_dir", return_value=os.path.join(temp_dir, "data")), \
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

    def test_run_in_sandbox_prefers_git_bash_when_available(self):
        fake_runtime = {
            "root": os.path.abspath("sandbox"),
            "bash": os.path.normpath("C:\\runtime\\bash.exe"),
            "python": "",
            "node": "",
            "npm": "",
            "npx": "",
        }
        with patch("core.sandbox_runtime.ensure_sandbox_runtime", return_value=fake_runtime), \
             patch("core.sandbox_runtime.build_sandbox_env", return_value={}), \
             patch("core.sandbox_runtime.subprocess.Popen") as popen:
            sandbox_runtime.run_in_sandbox("echo ok", cwd=os.getcwd(), shell_kind="bash")

        args = popen.call_args.args[0]
        self.assertEqual(args[:2], [fake_runtime["bash"], "-lc"])
        self.assertEqual(args[2], "echo ok")

    def test_run_in_sandbox_uses_cmd_fallback_when_git_bash_is_missing_on_windows(self):
        fake_runtime = {
            "root": os.path.abspath("sandbox"),
            "bash": "",
            "python": "",
            "node": "",
            "npm": "",
            "npx": "",
        }
        cmd_exe = os.path.normpath("C:\\Windows\\System32\\cmd.exe")
        with patch("core.sandbox_runtime.ensure_sandbox_runtime", return_value=fake_runtime), \
             patch("core.sandbox_runtime.build_sandbox_env", return_value={}), \
             patch("core.sandbox_runtime._resolve_cmd_exe", return_value=cmd_exe), \
             patch("core.sandbox_runtime._no_window_kwargs", return_value={}), \
             patch("core.sandbox_runtime.os.name", "nt"), \
             patch("core.sandbox_runtime.subprocess.Popen") as popen:
            sandbox_runtime.run_in_sandbox("node -v", cwd=os.getcwd(), shell_kind="bash")

        args = popen.call_args.args[0]
        self.assertEqual(args, [cmd_exe, "/d", "/s", "/c", "node -v"])

    def test_install_skill_dependencies_force_bypasses_cached_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = os.path.join(temp_dir, "sandbox")
            python_exe = os.path.join(temp_dir, "python", "python.exe")
            os.makedirs(os.path.dirname(python_exe), exist_ok=True)
            with open(python_exe, "w", encoding="utf-8") as f:
                f.write("")

            fake_runtime = {
                "root": runtime_root,
                "python": python_exe,
                "pip": python_exe,
                "node": "",
                "npm": "",
                "npx": "",
                "bash": "",
            }

            with patch("core.sandbox_runtime.ensure_sandbox_runtime", return_value=fake_runtime), \
                 patch("core.sandbox_runtime.get_app_data_dir", return_value=temp_dir), \
                 patch("core.sandbox_runtime.subprocess.check_output", return_value="installed") as check_output:
                first = sandbox_runtime.install_skill_dependencies("demo-skill", python_dependencies=["Pillow"], force=True)
                second = sandbox_runtime.install_skill_dependencies("demo-skill", python_dependencies=["Pillow"])
                third = sandbox_runtime.install_skill_dependencies("demo-skill", python_dependencies=["Pillow"], force=True)

        self.assertTrue(first["ok"])
        self.assertFalse(second["installed"])
        self.assertTrue(third["installed"])
        self.assertEqual(check_output.call_count, 2)


if __name__ == "__main__":
    unittest.main()
