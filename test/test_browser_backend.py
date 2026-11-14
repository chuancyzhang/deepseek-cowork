import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from browser_test_context import browser_config
from core.browser_backend import (BrowserBackendCoordinator, BrowserBackendError,
    INTERNAL, EXTERNAL, INTERNAL_TOOLS, WEB_TOOLS, bind_run, finish_run, browser_operation)
from core.execution_authorization import ExecutionAuthorization
from core.skill_manager import SkillManager
from core.tool_registry import TOOL_SOURCE_OPTIONAL
from core.workbench import WorkbenchBridge

ROOT = Path(__file__).resolve().parents[1]


class BrowserBackendTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = browser_config(temporary.name)
        self.coordinator = BrowserBackendCoordinator(self.config)

    def auth(self, run="run", session="chat", worker=True):
        auth = ExecutionAuthorization(session, run, True, "")
        bind_run(self.config, auth, worker=worker)
        return auth

    def test_default_persistence_both_directions_and_legacy_toggle(self):
        self.assertEqual(self.coordinator.snapshot()["backend"], EXTERNAL)
        self.config.config["disabled_skills"] = ["browser-automation"]
        self.assertTrue(self.config.is_skill_enabled("browser-automation"))
        first = self.coordinator.save(True)
        self.assertEqual(first["backend"], INTERNAL)
        self.assertEqual(BrowserBackendCoordinator(self.config).snapshot(), self.coordinator.snapshot())
        self.assertFalse(self.config.set_skill_enabled("browser-automation", True))
        second = self.coordinator.save(False)
        self.assertEqual(second["backend"], EXTERNAL)
        self.assertGreater(second["generation"], first["generation"])

    def test_wait_cancel_and_new_run_rejected_while_old_run_continues(self):
        auth = self.auth()
        context = {"execution_authorization": auth}
        with browser_operation(context, EXTERNAL):
            pending = self.coordinator.save(True)
            self.assertTrue(pending["pending"])
            new = self.auth("new")
            with self.assertRaisesRegex(BrowserBackendError, "暂不启动"):
                with browser_operation({"execution_authorization": new}, EXTERNAL): pass
            self.assertFalse(self.coordinator.save(False)["pending"])
        self.coordinator.save(True)
        with browser_operation(context, EXTERNAL): pass
        finish_run(auth)
        self.assertEqual(self.coordinator.snapshot()["backend"], INTERNAL)
        with self.assertRaises(BrowserBackendError):
            with browser_operation(context, EXTERNAL): pass

    def test_save_failure_keeps_disk_and_memory(self):
        before = Path(self.config.config_path).read_bytes()
        with patch.object(self.config, "_write_config_or_raise", side_effect=OSError("full")):
            with self.assertRaises(OSError): self.coordinator.save(True)
        self.assertEqual(Path(self.config.config_path).read_bytes(), before)
        self.assertNotIn("experimental_browser_automation", self.config.config)
        self.assertEqual(self.coordinator.snapshot()["backend"], EXTERNAL)

    def test_coordinator_failure_restores_saved_preference(self):
        with patch.object(self.coordinator, "_state", side_effect=OSError("database full")):
            with self.assertRaises(OSError): self.coordinator.save(True)
        self.assertEqual(self.coordinator.snapshot()["backend"], EXTERNAL)
        self.assertEqual(json.loads(Path(self.config.config_path).read_text())["experimental_browser_automation"], {"enabled": False})

    def test_old_pinned_generation_rejected_even_after_switching_back(self):
        auth = self.auth()
        self.coordinator.save(True); self.coordinator.save(False)
        with self.assertRaisesRegex(BrowserBackendError, "切换"):
            with browser_operation({"execution_authorization": auth}, EXTERNAL): pass

    def test_reconstructed_finished_run_cannot_reacquire_after_cleanup(self):
        auth = self.auth()
        snapshot = dict(auth._browser_state["snapshot"])
        with browser_operation({"execution_authorization": auth}, EXTERNAL): pass
        finish_run(auth)
        resumed = ExecutionAuthorization("chat", "run", True, "")
        bind_run(self.config, resumed, snapshot=snapshot)
        with self.assertRaisesRegex(BrowserBackendError, "已结束"):
            with browser_operation({"execution_authorization": resumed}, EXTERNAL): pass

    def test_cleanup_failure_blocks_switch_and_retry_stops_only_owned_session(self):
        auth = self.auth()
        with browser_operation({"execution_authorization": auth}, EXTERNAL) as (coordinator, _):
            coordinator.track_session("chat", "run", "owned")
        self.coordinator.save(True)
        with patch("core.browser_skill_component._stop_session_after_interruption", return_value={"stopped": False}) as stop:
            finish_run(auth)
            stop.assert_called_once_with("owned")
        self.assertTrue(self.coordinator.snapshot()["pending"])
        self.assertTrue(self.coordinator.snapshot()["cleanup_errors"])
        with patch("core.browser_skill_component._stop_session_after_interruption", return_value={"stopped": True}) as stop:
            self.coordinator.recover()
            stop.assert_called_once_with("owned")
        self.assertEqual(self.coordinator.snapshot()["backend"], INTERNAL)

    def test_finish_waits_inflight_operation_and_shared_workers(self):
        auth = self.auth()
        bind_run(self.config, auth, worker=True)
        with browser_operation({"execution_authorization": auth}, EXTERNAL):
            self.coordinator.save(True)
            finish_run(auth)
            self.assertFalse(auth._browser_state["finished"])
            finish_run(auth)
            self.assertTrue(self.coordinator.snapshot()["pending"])
        self.assertEqual(self.coordinator.snapshot()["backend"], INTERNAL)

    def test_concurrent_switch_and_acquire_are_serialized(self):
        auth = self.auth()
        barrier = threading.Barrier(2)
        results = []
        def acquire():
            barrier.wait()
            try:
                with browser_operation({"execution_authorization": auth}, EXTERNAL):
                    results.append("acquired")
            except BrowserBackendError:
                results.append("rejected")
        thread = threading.Thread(target=acquire); thread.start()
        barrier.wait(); state = self.coordinator.save(True); thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ["acquired"] if state["pending"] else ["rejected"])
        finish_run(auth)
        self.assertEqual(self.coordinator.snapshot()["backend"], INTERNAL)

    def manager(self):
        manager = SkillManager(config_manager=self.config, auto_load=False, load_mcp_tools=False)
        for name in ("workbench", "browser-automation"):
            manager.skill_source_kinds[name] = TOOL_SOURCE_OPTIONAL
            manager._load_legacy_implementation(name, str(ROOT / "ai_skills" / name / "impl.py"))
        return manager

    def test_exposure_search_validation_and_prefix_pinning(self):
        manager = self.manager()
        for backend in (EXTERNAL, INTERNAL):
            self.coordinator.save(backend == INTERNAL)
            context = {"browser_backend": {k: self.coordinator.snapshot()[k] for k in ("backend", "generation")}}
            manager.browser_run_context = context
            all_names = set(manager.tools)
            definitions = manager.get_tool_definitions(run_context=context, discovered_tool_names=all_names)
            names = {d["function"]["name"] for d in definitions}
            self.assertEqual("browser_skill_cli" in names, backend == EXTERNAL)
            self.assertEqual(WEB_TOOLS <= names, backend == INTERNAL)
            if backend == INTERNAL:
                direct = manager.get_tool_definitions(run_context=context)
                self.assertTrue(INTERNAL_TOOLS <= {d["function"]["name"] for d in direct})
                self.assertEqual(manager.get_full_skill_prompt("browser-automation"), "")
            else:
                opening = next(d["function"] for d in definitions if d["function"]["name"] == "browser_open")
                self.assertEqual(opening["parameters"]["properties"]["kind"]["enum"], ["file", "terminal"])
            forbidden = "browser_skill_cli" if backend == INTERNAL else "browser_action"
            self.assertFalse(manager.validate_tool_run_access(forbidden, run_context=context, discovered_tool_names=all_names)["ok"])
            found = manager._tool_search(forbidden, include_loaded=True, _context={"run_context": context})
            self.assertNotIn(forbidden, found["discovered_tools"])
            before = json.dumps(definitions, sort_keys=True)
            self.coordinator.save(backend != INTERNAL)
            after = manager.get_tool_definitions(run_context=context, discovered_tool_names=all_names)
            self.assertEqual(json.dumps(after, sort_keys=True), before)

    def test_bridge_rejects_wrong_backend_before_dispatch_and_stop_blocks(self):
        auth = self.auth()
        bridge = WorkbenchBridge()
        with patch.object(bridge, "_call") as dispatch:
            result = bridge.call("open", {"kind": "web"}, {"execution_authorization": auth})
            self.assertFalse(result["ok"]); dispatch.assert_not_called()
        auth.cancel()
        with self.assertRaises(Exception):
            with browser_operation({"execution_authorization": auth}, EXTERNAL): pass

    def test_expired_bridge_claim_is_not_given_a_new_deadline(self):
        bridge = WorkbenchBridge()
        with patch.object(bridge, "_guarded_call") as dispatch:
            result = bridge.call("open", {"kind": "file"}, {}, timeout=0)
        self.assertEqual(result["status"], "timeout")
        dispatch.assert_not_called()

    def test_external_session_creation_is_recoverable_and_foreign_calls_rejected(self):
        path = ROOT / "ai_skills/browser-automation/impl.py"
        spec = importlib.util.spec_from_file_location("backend_browser_impl", path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        auth = self.auth()
        context = {"execution_authorization": auth}
        def execute(args, **kwargs):
            if args[:2] == ["session", "request"]:
                return {"status": "completed", "result": {"request_id": args[2], "state": "active", "session": {"session_id": "owned"}}}
            return {"status": "completed", "result": {"session_id": "owned"}}
        with patch.object(module, "run_browser_skill_cli", side_effect=execute) as run:
            result = module.browser_skill_cli(["session", "start"], _context=context)
            self.assertEqual(result["status"], "completed")
            self.assertIn("--request-id", run.call_args_list[0].args[0])
            for args in (["snapshot", "--session", "other"], ["session", "stop", "--all"], ["snapshot"], ["session", "request", "other", "--cancel"]):
                self.assertEqual(module.browser_skill_cli(args, _context=context)["status"], "incomplete")
            self.assertEqual(run.call_count, 2)
        with patch("core.browser_skill_component._stop_session_after_interruption", return_value={"stopped": True}) as stop:
            finish_run(auth)
            stop.assert_called_once_with("owned")


if __name__ == "__main__": unittest.main()
