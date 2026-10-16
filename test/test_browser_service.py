import json
import os
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from core import browser_skill_component as component


class BrowserServiceTests(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        root = stack.enter_context(tempfile.TemporaryDirectory())
        stack.enter_context(patch.object(component, "get_app_data_dir", return_value=root))
        stack.enter_context(patch.object(component, "_browser_process_options", return_value={
            "cwd": root, "env": {"BSK_HOME": os.path.join(root, "home")},
        }))
        self.log = stack.enter_context(patch.object(component, "log_browser_skill_event"))
        self.probe = stack.enter_context(patch.object(component, "_probe_browser_skill_service"))
        self.launch = stack.enter_context(patch.object(component, "popen_external_program"))
        self.lock = stack.enter_context(patch.object(component, "acquire_process_singleton"))
        self.child = self.launch.return_value
        self.child.pid = 42
        self.child.poll.return_value = None

    def test_existing_service_is_reused_without_start_or_lock(self):
        self.probe.return_value = {"state": "ready", "pid": 18}
        result = component._ensure_browser_skill_service()
        self.assertEqual(result["mode"], "existing")
        self.launch.assert_not_called()
        self.lock.assert_not_called()

    def test_ambiguous_probe_error_never_starts_another_service(self):
        self.probe.return_value = {"state": "failed", "detail": "IPC access denied"}
        result = component._ensure_browser_skill_service()
        self.assertFalse(result["ready"])
        self.assertIn("IPC access denied", result["detail"])
        self.launch.assert_not_called()

    def test_cancel_before_start_preserves_existing_processes(self):
        result = component._ensure_browser_skill_service(lambda: True)
        self.assertEqual(result["code"], "aborted")
        self.probe.assert_not_called()
        self.launch.assert_not_called()

    def test_probe_timeout_terminates_only_owned_child_and_releases_lock(self):
        self.probe.side_effect = [
            {"state": "missing"}, {"state": "missing"},
            subprocess.TimeoutExpired("status", 3),
        ]
        result = component._ensure_browser_skill_service()
        self.assertEqual(result["code"], "browser_service_start_timeout")
        self.launch.assert_called_once()
        self.assertIn("--foreground", self.launch.call_args.args[0])
        self.child.terminate.assert_called_once()
        self.child.wait.assert_called_once()
        self.lock.return_value.release.assert_called_once()

    def test_cancel_after_launch_reaps_owned_child(self):
        self.probe.return_value = {"state": "missing"}
        cancelled = MagicMock(side_effect=[False, False, False, False, True])
        result = component._ensure_browser_skill_service(cancelled)
        self.assertEqual(result["code"], "aborted")
        self.child.terminate.assert_called_once()
        self.lock.return_value.release.assert_called_once()

    def test_probe_recognizes_only_explicit_absent_listener(self):
        # Call the actual probe, preserving its per-command connection wait.
        with patch.object(component, "_run_bsk_executable") as run:
            run.return_value = {"returncode": 2, "stdout": json.dumps({
                "message": "automatic daemon startup is disabled",
            }), "stderr": ""}
            result = REAL_PROBE()
            self.assertEqual(result["state"], "missing")
            self.assertEqual(run.call_args.kwargs["env_overrides"], {"BSK_BROWSER_WAIT_MS": "0"})
            run.return_value["stdout"] = '{"message":"permission denied"}'
            self.assertEqual(REAL_PROBE()["state"], "failed")


REAL_PROBE = component._probe_browser_skill_service
