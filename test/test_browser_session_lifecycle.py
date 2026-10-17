"""Recoverable BrowserSkill starts must follow the actual CLI handshake."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from browser_test_context import browser_config
from core import browser_skill_component as component
from core.browser_backend import BrowserBackendCoordinator, bind_run, finish_run
from core.execution_authorization import ExecutionAuthorization


class BrowserSessionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(tempfile.TemporaryDirectory())
        self.config = browser_config(self.root)
        self.coordinator = BrowserBackendCoordinator(self.config)
        self.auth = ExecutionAuthorization("chat", "run", True, "")
        bind_run(self.config, self.auth)
        self.context = {"execution_authorization": self.auth}
        path = Path(__file__).resolve().parents[1] / "ai_skills/browser-automation/impl.py"
        spec = importlib.util.spec_from_file_location("browser_lifecycle_impl", path)
        self.browser = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.browser)
        self.calls = []
        self.token = ""
        self.abort = {"aborted": False}
        self.prepare_state = "prepared"
        self.claim_state = "active"
        self.start_error = False
        self.cancel_after_prepare = False
        self.enterContext(patch.object(self.browser, "_init_abort_state", return_value=self.abort))
        self.enterContext(patch.object(self.browser, "log_browser_skill_event"))
        self.enterContext(patch.object(component, "log_browser_skill_event"))
        self.enterContext(patch.object(component, "browser_skill_status", return_value={"ready": True}))
        self.service = self.enterContext(patch.object(component, "_ensure_browser_skill_service", return_value={"ready": True}))
        self.enterContext(patch.object(component, "_browser_process_options", return_value={}))
        self.enterContext(patch.object(component, "popen_external_program", side_effect=self.launch))

    def launch(self, argv, **kwargs):
        args = argv[3:]
        self.calls.append(args)
        code = 0
        if "--help" in args:
            payload = "Session start help"
        elif args[:2] == ["session", "request"]:
            self.token = args[2]
            self.assertTrue(self.coordinator.owns_session("chat", "run", "request:" + self.token))
            state = self.prepare_state if args[-1] == "--prepare" else self.claim_state
            payload = {"request_id": self.token, "state": state}
            if args[-1] == "--claim":
                # Ownership must already be durable before the daemon disables
                # automatic cancellation of an unclaimed session.
                self.assertTrue(self.coordinator.owns_session("chat", "run", "abcd"))
                payload["session"] = {"session_id": "abcd"}
            elif self.cancel_after_prepare:
                self.abort["aborted"] = True
        elif args[:2] == ["session", "start"]:
            self.assertEqual(args[-2:], ["--request-id", self.token])
            self.assertEqual(self.calls[0][-1], "--prepare")
            payload = {"session_id": "abcd"}
            if self.start_error:
                code, payload = 1, {"code": "invalid_params", "message": "daemon restarted"}
        else:
            payload = {"stopped": ["abcd"]}
        kwargs["stdout"].write(json.dumps(payload).encode())
        return type("Process", (), {"pid": 42, "returncode": code, "poll": lambda _: code})()

    def start(self):
        return self.browser.browser_skill_cli(["session", "start"], _context=self.context)

    def test_prepare_start_persist_claim_then_stop(self):
        self.assertEqual(self.start()["status"], "completed")
        self.assertEqual([args[:2] for args in self.calls],
                         [["session", "request"], ["session", "start"], ["session", "request"]])
        self.assertEqual(self.calls[-1][-1], "--claim")
        self.service.assert_called_once()
        self.assertFalse(self.coordinator.owns_session("chat", "run", "request:" + self.token))
        self.assertTrue(self.coordinator.owns_session("chat", "run", "abcd"))
        result = self.browser.browser_skill_cli(["session", "stop", "abcd"], _context=self.context)
        self.assertEqual(result["status"], "completed")
        finish_run(self.auth)
        self.assertFalse(self.coordinator.run_active("chat", "run"))

    def test_unconfirmed_prepare_does_not_start(self):
        self.prepare_state = "closed"
        result = self.start()
        self.assertEqual(result["error"]["code"], "session_request_not_prepared")
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.coordinator.owns_session("chat", "run", "request:" + self.token))

    def test_cancel_after_prepare_does_not_create_window(self):
        self.cancel_after_prepare = True
        self.assertEqual(self.start()["error"]["code"], "aborted")
        self.assertEqual(len(self.calls), 1)

    def test_start_failure_keeps_request_for_cleanup_without_retry(self):
        self.start_error = True
        self.assertEqual(self.start()["status"], "incomplete")
        self.assertEqual(len(self.calls), 2)
        self.service.assert_called_once()
        self.assertTrue(self.coordinator.owns_session("chat", "run", "request:" + self.token))

    def test_claim_failure_retains_session_and_request(self):
        self.claim_state = "closed"
        result = self.start()
        self.assertEqual(result["error"]["code"], "session_claim_failed")
        self.assertEqual(result["result"]["session_id"], "abcd")
        self.assertTrue(self.coordinator.owns_session("chat", "run", "request:" + self.token))
        self.assertTrue(self.coordinator.owns_session("chat", "run", "abcd"))

    def test_help_does_not_start_service_or_prepare(self):
        result = self.browser.browser_skill_cli(["session", "start", "--help"], _context=self.context)
        self.assertEqual(result["status"], "completed")
        self.service.assert_not_called()
        self.assertEqual(self.calls, [["session", "start", "--help"]])

    def test_cancel_acknowledgement_is_not_cleanup_confirmation(self):
        self.start_error = True
        self.start()
        self.coordinator.save(True)
        for state in ("cancelling", "cleanup_failed", "unknown", "closed"):
            with self.subTest(state=state), patch.object(component, "_run_bsk", return_value={
                    "returncode": 0, "stdout": json.dumps({"request_id": self.token, "state": state}), "stderr": "",
            }) as cancel:
                self.coordinator.finish("chat", "run")
                self.assertEqual(cancel.call_args.args[0],
                                 ["--json", "--quiet", "session", "request", self.token, "--cancel"])
                snapshot = self.coordinator.snapshot()
                self.assertEqual(snapshot["pending"], state != "closed")
                self.assertEqual(bool(snapshot["cleanup_errors"]), state != "closed")


if __name__ == "__main__":
    unittest.main()
