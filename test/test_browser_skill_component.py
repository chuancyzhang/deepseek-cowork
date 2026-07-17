import hashlib
import io
import json
import os
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
                "communicate": lambda self, timeout=None: (
                    json.dumps({"ok": True}),
                    "",
                ),
            },
        )()
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
            return_value=process,
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
