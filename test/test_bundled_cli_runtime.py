"""Real Windows CLI smoke checks, also runnable against an isolated onedir build.

Set COWORK_CLI_SMOKE_DIST to the built deepseek-cowork directory to test its files.
Authentication uses a synthetic host and no credential store or network requests.
"""

import ast
from contextlib import ExitStack
import fnmatch
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from core import sandbox_runtime
from scripts import bundled_runtime_checks as checks


ROOT = Path(__file__).resolve().parents[1]


class RuntimeBuildChecks(unittest.TestCase):
    def test_slim_git_collection_keeps_authentication_graphics_library(self):
        tree = ast.parse((ROOT / "deepseek-cowork.spec").read_text(encoding="utf-8"))
        excludes = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "GIT_BASH_SLIM_EXCLUDES" for t in node.targets))
        for name in checks.GIT_REQUIRED_FILES:
            self.assertFalse(any(fnmatch.fnmatch(name, pattern) for pattern in excludes), name)

    @unittest.skipUnless(os.name == "nt", "PowerShell runtime preparation is Windows-only")
    def test_fetch_script_rejects_wrong_hash_and_version(self):
        # Load the exact function definitions without running downloads/extraction.
        script = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:COWORK_FETCH_SCRIPT, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw $errors[0] }
$functions = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)
foreach ($function in $functions) { . ([scriptblock]::Create($function.Extent.Text)) }
$hashRejected = $false
try { Assert-Hash -Path $env:COWORK_TEST_FILE -ExpectedSha256 ('0' * 64) } catch {
    if ($_.Exception.Message -notmatch 'SHA256 mismatch') { throw }
    $hashRejected = $true
}
if (-not $hashRejected) { throw 'Wrong archive hash was accepted.' }
Assert-Hash -Path $env:COWORK_TEST_FILE -ExpectedSha256 $env:COWORK_TEST_HASH
$versionRejected = $false
try { Assert-RuntimeVersion -Executable $env:COWORK_TEST_EXE -ExpectedFirstLine 'ripgrep 15.1.0' } catch {
    if ($_.Exception.Message -notmatch 'Unexpected runtime version') { throw }
    $versionRejected = $true
}
if (-not $versionRejected) { throw 'Wrong executable version was accepted.' }
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "archive.zip"
            data.write_bytes(b"test archive")
            exe = root / "wrong-version.cmd"
            exe.write_text("@echo off\necho ripgrep 0.0.0\nexit /b 0\n", encoding="ascii")
            # Let Windows PowerShell find its own built-in modules, rather than
            # inheriting a PowerShell 7 PSModulePath from the test runner.
            env = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
            env.update({"COWORK_FETCH_SCRIPT": str(ROOT / "scripts/fetch_runtimes.ps1"),
                   "COWORK_TEST_FILE": str(data), "COWORK_TEST_EXE": str(exe),
                   "COWORK_TEST_HASH": hashlib.sha256(data.read_bytes()).hexdigest().upper()})
            result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                    env=env, capture_output=True, text=True, encoding="utf-8", timeout=20,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(os.name == "nt", "Windows runtime smoke")
class BundledCliSmoke(unittest.TestCase):
    def setUp(self):
        dist = os.environ.get("COWORK_CLI_SMOKE_DIST")
        self.base = Path(dist).resolve() if dist else ROOT
        runtime_root = self.base / "_internal" if dist else self.base
        self.git_root = runtime_root / "git_bash_env"
        self.rg_root = runtime_root / "ripgrep_env"
        if not (self.rg_root / "rg.exe").is_file() or not (self.git_root / "cmd/git.exe").is_file():
            if dist:
                self.fail("The requested distribution is missing Git or ripgrep.")
            self.skipTest("Run scripts/fetch_runtimes.ps1 to enable real runtime smoke checks.")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="cowork-cli-")))
        self.workspace = self.temp / "中文 空格"
        self.workspace.mkdir()
        profile = self.temp / "profile"
        profile.mkdir()
        (profile / ".gitconfig").write_text("", encoding="utf-8")
        system = os.environ.get("SystemRoot", r"C:\Windows")
        # Deliberately remove the host Git/rg and all inherited Git/GCM settings.
        env = {key: value for key, value in os.environ.items() if key.upper() in {
            "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
        }}
        env.update({"PATH": str(Path(system) / "System32"), "HOME": str(profile),
                    "USERPROFILE": str(profile), "XDG_CONFIG_HOME": str(profile / ".config"),
                    "GIT_CONFIG_GLOBAL": str(profile / ".gitconfig")})
        self.stack.enter_context(patch.dict(os.environ, env, clear=True))
        self.stack.enter_context(patch.object(sandbox_runtime, "get_base_dir", return_value=str(self.base)))
        self.stack.enter_context(patch.object(sandbox_runtime, "get_app_data_dir", return_value=str(self.temp / "data")))
        self.stack.enter_context(patch.object(sandbox_runtime, "_is_frozen", return_value=bool(dist)))
        sandbox_runtime.reset_runtime_cache()
        self.addCleanup(sandbox_runtime.reset_runtime_cache)

    def run_command(self, command, *, input_text=None, extra_env=None):
        process = sandbox_runtime.run_in_sandbox(command, cwd=str(self.workspace), text=True, extra_env=extra_env)
        try:
            stdout, stderr = process.communicate(input=input_text, timeout=20)
        except subprocess.TimeoutExpired:
            sandbox_runtime.terminate_process_tree(process)
            process.communicate(timeout=5)
            self.fail("Bundled CLI did not finish (possible unexpected credential dialog).")
        return process.returncode, stdout, stderr

    def test_rg_searches_unicode_and_spaces_without_host_installation(self):
        checks.validate_ripgrep_bundle(self.rg_root)
        filename = "中文 文档.txt"
        (self.workspace / filename).write_text("first\nneedle 中文\n", encoding="utf-8")
        for command, expected in (
            ("rg --version", "ripgrep 15.1.0"),
            ("rg --files", filename),
            ("rg -n needle .", "2:needle 中文"),
        ):
            code, stdout, stderr = self.run_command(command)
            self.assertEqual(code, 0, stderr)
            self.assertIn(expected, stdout)
        code, _, _ = self.run_command("rg definitely-absent .")
        self.assertEqual(code, 1)
        self.assertEqual(Path(sandbox_runtime.get_runtime_executable("rg")), self.rg_root / "rg.exe")

    def test_manager_handles_two_authentication_requests_and_cancellation(self):
        checks.validate_git_bundle(self.git_root)
        trace = self.temp / "git-trace.log"
        askpass = self.temp / "askpass.cmd"
        askpass.write_text("@echo off\necho smoke-only-value\n", encoding="ascii")
        env = {
            "GIT_TRACE": str(trace), "GCM_TRACE": str(trace),
            "GCM_CREDENTIAL_STORE": "none", "GCM_PROVIDER": "generic", "GCM_AUTHORITY": "basic",
            # Test-only ASKPASS adapter exercises success/cancel without a real account UI.
            "GCM_GUI_PROMPT": "false", "GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0",
        }
        request = "protocol=https\nhost=cowork-auth-smoke.invalid\n\n"
        for _ in range(2):
            code, stdout, stderr = self.run_command("git credential fill", input_text=request, extra_env=env)
            self.assertEqual(code, 0, stderr)
            self.assertIn("password=smoke-only-value", stdout)
        text = trace.read_text(encoding="utf-8")
        self.assertNotIn("helper-selector", text)
        self.assertGreaterEqual(text.count("credential-manager get"), 2)
        self.assertIn("Prompting for basic credentials", text)
        askpass.write_text("@echo off\nexit /b 1\n", encoding="ascii")
        code, _, stderr = self.run_command("git credential fill", input_text=request, extra_env=env)
        self.assertNotEqual(code, 0)
        self.assertTrue(stderr.strip())
        # Cancellation did not prevent the next command from completing.
        self.assertEqual(self.run_command("git --version")[0], 0)


if __name__ == "__main__":
    unittest.main()
