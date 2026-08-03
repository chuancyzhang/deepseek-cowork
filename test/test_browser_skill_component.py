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


def _archive_bytes(executable=b"demo"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("bsk.exe", executable)
    return stream.getvalue()


def _extension_archive_bytes(version=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({
                "manifest_version": 3,
                "name": "BrowserSkill",
                "version": version or browser_skill_component.BROWSER_SKILL_EXTENSION_VERSION,
            }),
        )
        archive.writestr("service-worker.js", "// fixture")
    return stream.getvalue()


class BrowserSkillComponentTests(unittest.TestCase):
    def _write_artifact(self, temp_dir, content, name="artifact.zip"):
        path = os.path.join(temp_dir, name)
        with open(path, "wb") as handle:
            handle.write(content)
        return path

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
                    "source": "bundled",
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
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = self._write_artifact(temp_dir, archive)
            with patch.object(
                browser_skill_component,
                "browser_skill_root",
                return_value=os.path.join(temp_dir, "browser-skill"),
            ), patch.object(
                browser_skill_component,
                "_bundled_artifact_status",
                return_value={"available": True, "path": archive_path},
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
        completed = {
            "returncode": 0,
            "stdout": f"bsk {browser_skill_component.BROWSER_SKILL_VERSION}",
            "stderr": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = self._write_artifact(temp_dir, archive)
            with patch.object(
                browser_skill_component,
                "browser_skill_root",
                return_value=os.path.join(temp_dir, "browser-skill"),
            ), patch.object(
                browser_skill_component,
                "_bundled_artifact_status",
                return_value={"available": True, "path": archive_path},
            ), patch.object(
                browser_skill_component,
                "BROWSER_SKILL_SHA256",
                digest,
            ), patch.object(
                browser_skill_component,
                "_run_bsk_executable",
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
                self.assertEqual(marker["source"], "bundled")

    def test_repair_stops_managed_daemon_before_replacing_cli(self):
        archive = _archive_bytes()
        digest = hashlib.sha256(archive).hexdigest().upper()
        completed = {
            "returncode": 0,
            "stdout": f"bsk {browser_skill_component.BROWSER_SKILL_VERSION}",
            "stderr": "",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._installed_component(temp_dir)
            archive_path = self._write_artifact(temp_dir, archive)
            with patch.object(
                browser_skill_component,
                "browser_skill_root",
                return_value=root,
            ), patch.object(
                browser_skill_component,
                "_bundled_artifact_status",
                return_value={"available": True, "path": archive_path},
            ), patch.object(
                browser_skill_component,
                "BROWSER_SKILL_SHA256",
                digest,
            ), patch.object(
                browser_skill_component,
                "_run_bsk_executable",
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
        ), patch.object(browser_skill_component, "popen_external_program") as popen:
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
                {"returncode": 0, "stdout": f"bsk {browser_skill_component.BROWSER_SKILL_VERSION}", "stderr": ""},
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
                {"returncode": 0, "stdout": f"bsk {browser_skill_component.BROWSER_SKILL_VERSION}", "stderr": ""},
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
            browser_skill_component,
            "popen_external_program",
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
            browser_skill_component,
            "popen_external_program",
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
            browser_skill_component,
            "popen_external_program",
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
            browser_skill_component,
            "popen_external_program",
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
                {"returncode": 0, "stdout": f"bsk {browser_skill_component.BROWSER_SKILL_VERSION}", "stderr": ""},
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

    def test_existing_017_install_is_reported_as_needing_update(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._installed_component(temp_dir)
            with patch.object(
                browser_skill_component,
                "browser_skill_root",
                return_value=root,
            ), patch.object(
                browser_skill_component,
                "_run_bsk",
                return_value={"returncode": 0, "stdout": "bsk 0.1.7", "stderr": ""},
            ):
                status = browser_skill_component.browser_skill_status()
        self.assertTrue(status["needs_update"])
        self.assertEqual(status["state"], "version_mismatch")
        self.assertEqual(status["version"], "0.1.7")

    def test_bundle_status_surfaces_missing_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            browser_skill_component,
            "browser_skill_bundle_root",
            return_value=temp_dir,
        ):
            status = browser_skill_component._bundled_artifact_status("cli")
        self.assertFalse(status["available"])
        self.assertIn("清单缺失", status["error"])

    def test_prepare_extension_verifies_manifest_and_publishes_stable_path(self):
        archive = _extension_archive_bytes()
        digest = hashlib.sha256(archive).hexdigest().upper()
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = self._write_artifact(temp_dir, archive, "extension.zip")
            component_root = os.path.join(temp_dir, "browser-skill-extension")
            with patch.object(
                browser_skill_component,
                "browser_skill_extension_component_root",
                return_value=component_root,
            ), patch.object(
                browser_skill_component,
                "_bundled_artifact_status",
                return_value={"available": True, "path": archive_path},
            ), patch.object(
                browser_skill_component,
                "BROWSER_SKILL_EXTENSION_SHA256",
                digest,
            ), patch.object(
                browser_skill_component,
                "browser_skill_status",
                return_value={"extension_prepared": True},
            ), patch.object(
                browser_skill_component,
                "log_browser_skill_event",
            ):
                result = browser_skill_component.prepare_browser_skill_extension()
                stable_path = browser_skill_component.browser_skill_extension_path()
                prepared = browser_skill_component._extension_preparation_status()
                manifest_exists = os.path.isfile(os.path.join(stable_path, "manifest.json"))
        self.assertTrue(result["extension_prepared"])
        self.assertTrue(prepared["prepared"])
        self.assertEqual(prepared["path"], stable_path)
        self.assertTrue(manifest_exists)

    def test_invalid_extension_version_preserves_existing_directory(self):
        archive = _extension_archive_bytes(version="9.9.9")
        digest = hashlib.sha256(archive).hexdigest().upper()
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = self._write_artifact(temp_dir, archive, "extension.zip")
            component_root = os.path.join(temp_dir, "browser-skill-extension")
            os.makedirs(os.path.join(component_root, "extension"))
            sentinel = os.path.join(component_root, "extension", "sentinel.txt")
            with open(sentinel, "w", encoding="utf-8") as handle:
                handle.write("old")
            with patch.object(
                browser_skill_component,
                "browser_skill_extension_component_root",
                return_value=component_root,
            ), patch.object(
                browser_skill_component,
                "_bundled_artifact_status",
                return_value={"available": True, "path": archive_path},
            ), patch.object(
                browser_skill_component,
                "BROWSER_SKILL_EXTENSION_SHA256",
                digest,
            ), patch.object(
                browser_skill_component,
                "log_browser_skill_event",
            ):
                with self.assertRaisesRegex(RuntimeError, "扩展验证失败"):
                    browser_skill_component.prepare_browser_skill_extension()
            self.assertTrue(os.path.isfile(sentinel))

    def test_atomic_replace_rolls_back_when_publish_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staged = os.path.join(temp_dir, "staged")
            target = os.path.join(temp_dir, "target")
            os.makedirs(staged)
            os.makedirs(target)
            with open(os.path.join(staged, "value.txt"), "w", encoding="utf-8") as handle:
                handle.write("new")
            with open(os.path.join(target, "value.txt"), "w", encoding="utf-8") as handle:
                handle.write("old")
            real_replace = os.replace

            def fail_publish(source, destination):
                if os.path.abspath(source) == os.path.abspath(staged):
                    raise OSError("publish failed")
                return real_replace(source, destination)

            with patch.object(browser_skill_component.os, "replace", side_effect=fail_publish):
                with self.assertRaisesRegex(OSError, "publish failed"):
                    browser_skill_component._replace_component_root(staged, target)
            with open(os.path.join(target, "value.txt"), "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "old")

    def test_browser_discovery_can_offer_edge_when_chrome_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            edge = os.path.join(temp_dir, "msedge.exe")
            with open(edge, "wb") as handle:
                handle.write(b"fixture")

            def candidates(browser_id):
                return [os.path.join(temp_dir, "missing-chrome.exe")] if browser_id == "chrome" else [edge]

            with patch.object(
                browser_skill_component,
                "_browser_candidate_paths",
                side_effect=candidates,
            ):
                browsers = browser_skill_component.browser_skill_browser_candidates()
        self.assertEqual([item["id"] for item in browsers], ["edge"])

    def test_chrome_launch_failure_does_not_silently_switch_to_edge(self):
        browsers = [
            {"id": "chrome", "name": "Google Chrome", "path": r"C:\chrome.exe", "extensions_url": "chrome://extensions/"},
            {"id": "edge", "name": "Microsoft Edge", "path": r"C:\edge.exe", "extensions_url": "edge://extensions/"},
        ]
        with patch.object(
            browser_skill_component,
            "browser_skill_browser_candidates",
            return_value=browsers,
        ), patch.object(
            browser_skill_component,
            "popen_external_program",
            side_effect=OSError("blocked"),
        ) as popen, patch.object(
            browser_skill_component,
            "log_browser_skill_event",
        ):
            with self.assertRaisesRegex(RuntimeError, "明确改选 Microsoft Edge"):
                browser_skill_component.launch_browser_skill_extension_manager("chrome")
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0][0], r"C:\chrome.exe")

    def test_doctor_classifies_browser_protocol_incompatibility(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = self._installed_component(temp_dir)
            doctor_payload = [
                {"name": "daemon running", "ok": True, "status": "ok"},
                {"name": "protocol compatible", "ok": True, "status": "ok"},
                {"name": "extension connected", "ok": True, "status": "ok"},
                {
                    "name": "browser protocol compatible",
                    "ok": False,
                    "status": "fail",
                    "detail": "extension 0.1.3 is incompatible",
                },
            ]
            command_results = [
                {"returncode": 0, "stdout": f"bsk {browser_skill_component.BROWSER_SKILL_VERSION}", "stderr": ""},
                {"returncode": 1, "stdout": json.dumps(doctor_payload), "stderr": ""},
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
            ) as probe, patch.object(
                browser_skill_component,
                "log_browser_skill_event",
            ):
                status = browser_skill_component.browser_skill_status(run_diagnostics=True)
        self.assertEqual(status["state"], "extension_incompatible")
        self.assertTrue(status["protocol_incompatible"])
        self.assertIn("0.1.3", status["health_error"])
        probe.assert_not_called()

    def test_agent_skill_doctor_failure_does_not_block_real_probe(self):
        payload = [
            {"name": "agent skill up to date", "ok": False, "status": "fail"},
            {"name": "daemon running", "ok": True, "status": "ok"},
            {"name": "protocol compatible", "ok": True, "status": "ok"},
            {"name": "extension connected", "ok": True, "status": "ok"},
            {"name": "browser protocol compatible", "ok": True, "status": "ok"},
        ]
        classification = browser_skill_component._classify_doctor_result(payload, "")
        self.assertEqual(classification["kind"], "connected")

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
