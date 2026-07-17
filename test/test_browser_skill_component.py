import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from core import browser_skill_component


class _Response:
    def __init__(self, content):
        self.content = content
        self.headers = {"content-length": str(len(content))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, _size):
        yield self.content


def _archive_bytes(executable=b"demo"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("bsk.exe", executable)
    return stream.getvalue()


class BrowserSkillComponentTests(unittest.TestCase):
    def _installed_component(self, temp_dir):
        root = os.path.join(temp_dir, "browser-skill")
        os.makedirs(root)
        with open(os.path.join(root, "bsk.exe"), "wb") as handle:
            handle.write(b"demo")
        with open(os.path.join(root, "component.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "schema": browser_skill_component.BROWSER_SKILL_MARKER_SCHEMA,
                    "version": browser_skill_component.BROWSER_SKILL_VERSION,
                    "sha256": browser_skill_component.BROWSER_SKILL_SHA256,
                    "verified": True,
                },
                handle,
            )
        return root

    def test_status_is_not_installed_without_executable(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            browser_skill_component,
            "browser_skill_root",
            return_value=os.path.join(temp_dir, "browser-skill"),
        ):
            status = browser_skill_component.browser_skill_status()
        self.assertFalse(status["installed"])
        self.assertEqual(status["state"], "not_installed")

    def test_install_rejects_sha256_mismatch_without_replacing_existing(self):
        archive = _archive_bytes()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            browser_skill_component,
            "browser_skill_root",
            return_value=os.path.join(temp_dir, "browser-skill"),
        ), patch.object(
            browser_skill_component.requests,
            "get",
            return_value=_Response(archive),
        ), patch.object(
            browser_skill_component,
            "BROWSER_SKILL_SHA256",
            "0" * 64,
        ), patch.object(
            browser_skill_component,
            "log_browser_skill_event",
        ):
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                browser_skill_component.install_browser_skill()
            self.assertFalse(
                os.path.exists(browser_skill_component.browser_skill_root())
            )

    def test_install_verifies_and_atomically_publishes_cli(self):
        archive = _archive_bytes()
        digest = hashlib.sha256(archive).hexdigest().upper()
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "bsk 0.1.7", "stderr": ""},
        )()
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            browser_skill_component,
            "browser_skill_root",
            return_value=os.path.join(temp_dir, "browser-skill"),
        ), patch.object(
            browser_skill_component.requests,
            "get",
            return_value=_Response(archive),
        ), patch.object(
            browser_skill_component,
            "BROWSER_SKILL_SHA256",
            digest,
        ), patch.object(
            browser_skill_component.subprocess,
            "run",
            return_value=completed,
        ), patch.object(
            browser_skill_component,
            "browser_skill_status",
            return_value={"installed": True},
        ), patch.object(
            browser_skill_component,
            "log_browser_skill_event",
        ):
            status = browser_skill_component.install_browser_skill()
            marker_path = os.path.join(
                browser_skill_component.browser_skill_root(),
                "component.json",
            )
            with open(marker_path, "r", encoding="utf-8") as handle:
                marker = json.load(handle)
            self.assertTrue(status["installed"])
            self.assertTrue(os.path.isfile(
                browser_skill_component.browser_skill_executable()
            ))
            self.assertEqual(marker["sha256"], digest)

    def test_repair_stops_managed_daemon_before_replacing_cli(self):
        archive = _archive_bytes()
        digest = hashlib.sha256(archive).hexdigest().upper()
        completed = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "bsk 0.1.7", "stderr": ""},
        )()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._installed_component(temp_dir)
            with patch.object(
                browser_skill_component,
                "browser_skill_root",
                return_value=root,
            ), patch.object(
                browser_skill_component.requests,
                "get",
                return_value=_Response(archive),
            ), patch.object(
                browser_skill_component,
                "BROWSER_SKILL_SHA256",
                digest,
            ), patch.object(
                browser_skill_component.subprocess,
                "run",
                return_value=completed,
            ), patch.object(
                browser_skill_component,
                "_stop_managed_daemon",
                return_value={"attempted": True, "stopped": True},
            ) as stop_daemon, patch.object(
                browser_skill_component,
                "browser_skill_status",
                return_value={"installed": True},
            ), patch.object(
                browser_skill_component,
                "log_browser_skill_event",
            ):
                browser_skill_component.install_browser_skill()
        stop_daemon.assert_called_once()

    def test_uninstall_stops_daemon_before_removing_cli(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._installed_component(temp_dir)
            with patch.object(
                browser_skill_component,
                "browser_skill_root",
                return_value=root,
            ), patch.object(
                browser_skill_component,
                "_stop_managed_daemon",
                return_value={"attempted": True, "stopped": True},
            ) as stop_daemon, patch.object(
                browser_skill_component,
                "browser_skill_status",
                return_value={"installed": False},
            ), patch.object(
                browser_skill_component,
                "log_browser_skill_event",
            ):
                status = browser_skill_component.uninstall_browser_skill()
        stop_daemon.assert_called_once()
        self.assertFalse(status["installed"])
        self.assertFalse(os.path.exists(root))

    def test_component_directory_removal_retries_windows_file_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = os.path.join(temp_dir, "component")
            os.makedirs(target)
            original_rmtree = browser_skill_component.shutil.rmtree
            attempts = {"count": 0}

            def flaky_rmtree(path):
                attempts["count"] += 1
                if attempts["count"] == 1:
                    raise PermissionError("file is in use")
                return original_rmtree(path)

            with patch.object(
                browser_skill_component.shutil,
                "rmtree",
                side_effect=flaky_rmtree,
            ), patch.object(browser_skill_component.time, "sleep"):
                browser_skill_component._remove_tree_with_retry(target)
        self.assertEqual(attempts["count"], 2)
        self.assertFalse(os.path.exists(target))

    def test_cli_reports_not_ready_without_starting_process(self):
        with patch.object(
            browser_skill_component,
            "browser_skill_status",
            return_value={
                "installed": True,
                "ready": False,
                "state_text": "CLI 已安装，扩展未连接",
                "health_error": "extension disconnected",
            },
        ), patch.object(browser_skill_component.subprocess, "Popen") as popen:
            result = browser_skill_component.run_browser_skill_cli(
                ["snapshot", "--session", "abcd"]
            )
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["error"]["code"], "browser_skill_not_ready")
        popen.assert_not_called()

    def test_doctor_reports_extension_disconnected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._installed_component(temp_dir)
            command_results = [
                {"returncode": 0, "stdout": "bsk 0.1.7", "stderr": ""},
                {
                    "returncode": 1,
                    "stdout": json.dumps({
                        "checks": [
                            {
                                "name": "extension connected",
                                "status": "FAIL",
                                "hint": "0 browsers connected",
                            }
                        ]
                    }),
                    "stderr": "",
                },
            ]
            with patch.object(
                browser_skill_component,
                "browser_skill_root",
                return_value=root,
            ), patch.object(
                browser_skill_component,
                "_run_bsk",
                side_effect=command_results,
            ), patch.object(
                browser_skill_component,
                "browser_skill_execution_probe",
                return_value={
                    "ok": True,
                    "elapsed_ms": 12,
                    "tab_count": 1,
                    "error": "",
                },
            ), patch.object(
                browser_skill_component,
                "log_browser_skill_event",
            ):
                status = browser_skill_component.browser_skill_status(
                    run_diagnostics=True
                )
        self.assertEqual(status["state"], "extension_disconnected")
        self.assertFalse(status["ready"])

    def test_doctor_reports_ready_browser(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._installed_component(temp_dir)
            command_results = [
                {"returncode": 0, "stdout": "bsk 0.1.7", "stderr": ""},
                {
                    "returncode": 0,
                    "stdout": json.dumps({
                        "checks": [
                            {
                                "name": "extension connected",
                                "status": "OK",
                            }
                        ]
                    }),
                    "stderr": "",
                },
            ]
            with patch.object(
                browser_skill_component,
                "browser_skill_root",
                return_value=root,
            ), patch.object(
                browser_skill_component,
                "_run_bsk",
                side_effect=command_results,
            ), patch.object(
                browser_skill_component,
                "browser_skill_execution_probe",
                return_value={
                    "ok": True,
                    "elapsed_ms": 12,
                    "tab_count": 1,
                    "error": "",
                },
            ), patch.object(
                browser_skill_component,
                "log_browser_skill_event",
            ):
                status = browser_skill_component.browser_skill_status(
                    run_diagnostics=True
                )
        self.assertEqual(status["state"], "ready")
        self.assertTrue(status["ready"])

    def test_cli_starts_process_with_argument_list_and_no_shell(self):
        process = type(
            "Process",
            (),
            {
                "pid": 42,
                "returncode": 0,
                "poll": lambda self: 0,
            },
        )()
        def start_process(_args, **kwargs):
            kwargs["stdout"].write(json.dumps({"ok": True}).encode("utf-8"))
            return process

        with patch.object(
            browser_skill_component,
            "browser_skill_status",
            return_value={"installed": True, "ready": True},
        ), patch.object(
            browser_skill_component,
            "browser_skill_executable",
            return_value=r"C:\components\bsk.exe",
        ), patch.object(
            browser_skill_component.subprocess,
            "Popen",
            side_effect=start_process,
        ) as popen, patch.object(
            browser_skill_component,
            "log_browser_skill_event",
        ):
            result = browser_skill_component.run_browser_skill_cli(
                ["snapshot", "--session", "abcd"]
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            popen.call_args.args[0],
            [
                r"C:\components\bsk.exe",
                "--json",
                "--quiet",
                "snapshot",
                "--session",
                "abcd",
            ],
        )
        self.assertNotIn("shell", popen.call_args.kwargs)

    def test_large_stdout_and_stderr_do_not_block_process_completion(self):
        payload = json.dumps({"tabs": [{"title": "x" * 256}] * 80})
        code = (
            "import sys\n"
            f"payload={payload!r}\n"
            "sys.stdout.write(payload)\n"
            "sys.stderr.write('e' * 100000)\n"
        )
        real_popen = subprocess.Popen

        def start_large_output_process(_args, **kwargs):
            return real_popen(
                [sys.executable, "-c", code],
                stdin=kwargs["stdin"],
                stdout=kwargs["stdout"],
                stderr=kwargs["stderr"],
            )

        with patch.object(
            browser_skill_component,
            "browser_skill_status",
            return_value={"installed": True, "ready": True},
        ), patch.object(
            browser_skill_component.subprocess,
            "Popen",
            side_effect=start_large_output_process,
        ), patch.object(
            browser_skill_component,
            "log_browser_skill_event",
        ):
            result = browser_skill_component.run_browser_skill_cli(
                ["tab", "list", "--session", "abcd"],
                timeout_seconds=5,
            )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"], json.loads(payload))
        self.assertEqual(len(result["stderr"]), 100000)

    def test_outer_timeout_reports_successful_session_cleanup(self):
        class BlockingProcess:
            pid = 42
            returncode = None

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = -15

            def kill(self):
                self.returncode = -9

            def wait(self, timeout=None):
                return self.returncode

        with patch.object(
            browser_skill_component,
            "browser_skill_status",
            return_value={"installed": True, "ready": True},
        ), patch.object(
            browser_skill_component.subprocess,
            "Popen",
            return_value=BlockingProcess(),
        ), patch.object(
            browser_skill_component,
            "_stop_session_after_interruption",
            return_value={
                "attempted": True,
                "session_id": "abcd",
                "stopped": True,
                "exit_code": 0,
                "error": "",
            },
        ), patch.object(
            browser_skill_component,
            "log_browser_skill_event",
        ):
            result = browser_skill_component.run_browser_skill_cli(
                ["tab", "list", "--session", "abcd"],
                timeout_seconds=1,
            )
        self.assertEqual(result["error"]["code"], "timeout")
        self.assertTrue(result["session_cleanup"]["stopped"])
        self.assertIn("Cowork 已结束", result["error"]["message"])

    def test_browser_skill_timeout_exit_does_not_force_session_cleanup(self):
        process = type(
            "Process",
            (),
            {
                "pid": 42,
                "returncode": 4,
                "poll": lambda self: 4,
            },
        )()
        def start_timeout_process(_args, **kwargs):
            kwargs["stderr"].write(json.dumps({
                "code": "timeout",
                "message": "tool RPC timed out",
            }).encode("utf-8"))
            return process

        with patch.object(
            browser_skill_component,
            "browser_skill_status",
            return_value={"installed": True, "ready": True},
        ), patch.object(
            browser_skill_component.subprocess,
            "Popen",
            side_effect=start_timeout_process,
        ), patch.object(
            browser_skill_component,
            "_stop_session_after_interruption",
        ) as cleanup, patch.object(
            browser_skill_component,
            "log_browser_skill_event",
        ):
            result = browser_skill_component.run_browser_skill_cli(
                ["tab", "list", "--session", "abcd"],
            )
        self.assertEqual(result["error"]["code"], "bsk_command_failed")
        self.assertEqual(result["error"]["exit_code"], 4)
        cleanup.assert_not_called()

    def test_repeated_session_cleanup_is_idempotent(self):
        with patch.object(
            browser_skill_component,
            "_run_bsk",
            return_value={
                "returncode": 1,
                "stdout": json.dumps({
                    "code": "not_found",
                    "message": "session is not registered",
                }),
                "stderr": "",
            },
        ), patch.object(
            browser_skill_component,
            "log_browser_skill_event",
        ):
            result = browser_skill_component._stop_session_after_interruption(
                "abcd"
            )
        self.assertTrue(result["stopped"])
        self.assertTrue(result["already_stopped"])
        self.assertEqual(result["error"], "")

    def test_execution_probe_lists_only_agent_tabs_and_always_stops(self):
        command_results = [
            {
                "returncode": 0,
                "stdout": json.dumps({"session_id": "abcd"}),
                "stderr": "",
            },
            {
                "returncode": 0,
                "stdout": json.dumps({"tabs": [{"tab_id": 1}]}),
                "stderr": "",
            },
            {"returncode": 0, "stdout": "{}", "stderr": ""},
        ]
        with patch.object(
            browser_skill_component,
            "_run_bsk",
            side_effect=command_results,
        ) as run, patch.object(
            browser_skill_component,
            "log_browser_skill_event",
        ):
            result = browser_skill_component.browser_skill_execution_probe()
        self.assertTrue(result["ok"])
        self.assertTrue(result["session_cleanup"]["stopped"])
        self.assertEqual(
            run.call_args_list[1].args[0][-2:],
            ["--scope", "agent"],
        )
        self.assertEqual(
            run.call_args_list[2].args[0][-3:],
            ["session", "stop", "abcd"],
        )

    def test_doctor_ready_but_probe_timeout_is_not_ready(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._installed_component(temp_dir)
            command_results = [
                {"returncode": 0, "stdout": "bsk 0.1.7", "stderr": ""},
                {
                    "returncode": 0,
                    "stdout": json.dumps([
                        {
                            "name": "extension connected",
                            "status": "ok",
                            "ok": True,
                        }
                    ]),
                    "stderr": "",
                },
            ]
            with patch.object(
                browser_skill_component,
                "browser_skill_root",
                return_value=root,
            ), patch.object(
                browser_skill_component,
                "_run_bsk",
                side_effect=command_results,
            ), patch.object(
                browser_skill_component,
                "browser_skill_execution_probe",
                return_value={
                    "ok": False,
                    "error": "浏览器执行通道在 45000ms 内未响应。",
                    "code": "execution_unresponsive",
                },
            ), patch.object(
                browser_skill_component,
                "log_browser_skill_event",
            ):
                status = browser_skill_component.browser_skill_status(
                    run_diagnostics=True
                )
        self.assertEqual(status["state"], "execution_unresponsive")
        self.assertFalse(status["ready"])
        self.assertIn("probe", status["diagnostics"])

    def test_safe_extract_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "bad.zip")
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../bsk.exe", b"bad")
            with self.assertRaisesRegex(RuntimeError, "不安全路径"):
                browser_skill_component._safe_extract_zip(
                    archive_path,
                    os.path.join(temp_dir, "extract"),
                )


if __name__ == "__main__":
    unittest.main()
