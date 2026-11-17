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

    def select(self, backend):
        self.config.set_skill_enabled("browser-automation", backend == EXTERNAL, persist_strict=True)
        return self.coordinator.snapshot()

    def test_default_internal_and_explicit_skill_choice(self):
        self.config.config = {"experimental_browser_automation": {"enabled": False}}
        self.config._write_config_or_raise()
        self.assertEqual(self.coordinator.snapshot()["backend"], INTERNAL)
        self.assertFalse(self.config.is_skill_enabled("browser-automation"))
        first = self.select(EXTERNAL)
        self.assertEqual(first["backend"], EXTERNAL)
        self.assertTrue(self.config.is_skill_enabled("browser-automation"))
        second = self.select(INTERNAL)
        self.assertEqual(second["backend"], INTERNAL)
        self.assertGreater(second["generation"], first["generation"])

    def test_active_external_and_new_internal_runs_coexist(self):
        old = self.auth()
        with browser_operation({"execution_authorization": old}, EXTERNAL):
            self.assertFalse(self.select(INTERNAL)["pending"])
            new = self.auth("new")
            with browser_operation({"execution_authorization": new}, INTERNAL): pass
            with self.assertRaises(BrowserBackendError):
                with browser_operation({"execution_authorization": new}, EXTERNAL): pass
        with browser_operation({"execution_authorization": old}, EXTERNAL): pass
        finish_run(old)
        with self.assertRaises(BrowserBackendError):
            with browser_operation({"execution_authorization": old}, EXTERNAL): pass

    def test_legacy_switch_migrates_without_inventing_skill_enablement(self):
        from core.config_manager import ConfigManager
        for old, enabled, disabled in ((False, [], []), (True, [], []), (True, ["browser-automation"], []), (False, ["browser-automation"], ["browser-automation"])):
            with self.subTest(old=old, enabled=enabled, disabled=disabled), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "config.json"
                path.write_text(json.dumps({"experimental_browser_automation": {"enabled": old}, "enabled_skills": enabled,
                                            "disabled_skills": disabled, "skill_configs": {"browser-automation": {"retained": "yes"}}}), encoding="utf-8")
                with patch("core.config_manager.get_app_data_dir", return_value=directory):
                    config = ConfigManager(migrate_legacy=False)
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.assertNotIn("experimental_browser_automation", saved)
                self.assertEqual(config.is_skill_enabled("browser-automation"), bool(enabled and not disabled))
                self.assertEqual(saved["skill_configs"]["browser-automation"]["retained"], "yes")

    def test_save_failure_keeps_disk_and_memory(self):
        before = Path(self.config.config_path).read_bytes()
        with patch.object(self.config, "_write_config_or_raise", side_effect=OSError("full")):
            with self.assertRaises(OSError): self.select(INTERNAL)
        self.assertEqual(Path(self.config.config_path).read_bytes(), before)
        self.assertTrue(self.config.is_skill_enabled("browser-automation"))
        self.assertEqual(self.coordinator.snapshot()["backend"], EXTERNAL)

    def test_pin_remains_valid_before_first_browser_call(self):
        auth = self.auth()
        self.select(INTERNAL)
        with browser_operation({"execution_authorization": auth}, EXTERNAL): pass
        self.select(EXTERNAL)
        with browser_operation({"execution_authorization": auth}, EXTERNAL): pass

    def test_component_repair_refuses_active_external_lease(self):
        auth = self.auth()
        with browser_operation({"execution_authorization": auth}, EXTERNAL):
            with self.assertRaisesRegex(BrowserBackendError, "尚未结束"):
                with self.coordinator.maintenance(): pass
        finish_run(auth)
        with self.coordinator.maintenance(): pass

    def test_reconstructed_finished_run_cannot_reacquire_after_cleanup(self):
        auth = self.auth()
        snapshot = dict(auth._browser_state["snapshot"])
        with browser_operation({"execution_authorization": auth}, EXTERNAL): pass
        finish_run(auth)
        resumed = ExecutionAuthorization("chat", "run", True, "")
        bind_run(self.config, resumed, snapshot=snapshot)
        with self.assertRaisesRegex(BrowserBackendError, "已结束"):
            with browser_operation({"execution_authorization": resumed}, EXTERNAL): pass

    def test_cleanup_failure_does_not_block_internal_and_retry_stops_only_owned_session(self):
        auth = self.auth()
        with browser_operation({"execution_authorization": auth}, EXTERNAL) as (coordinator, _):
            coordinator.track_session("chat", "run", "owned")
        self.select(INTERNAL)
        with patch("core.browser_skill_component._stop_session_after_interruption", return_value={"stopped": False}) as stop:
            finish_run(auth)
            stop.assert_called_once_with("owned")
        self.assertFalse(self.coordinator.snapshot()["pending"])
        self.assertTrue(self.coordinator.snapshot()["cleanup_errors"])
        with patch("core.browser_skill_component._stop_session_after_interruption", return_value={"stopped": True}) as stop:
            self.coordinator.recover()
            stop.assert_called_once_with("owned")
        self.assertEqual(self.coordinator.snapshot()["backend"], INTERNAL)

    def test_finish_waits_inflight_operation_and_shared_workers(self):
        auth = self.auth()
        bind_run(self.config, auth, worker=True)
        with browser_operation({"execution_authorization": auth}, EXTERNAL):
            self.select(INTERNAL)
            finish_run(auth)
            self.assertFalse(auth._browser_state["finished"])
            finish_run(auth)
            self.assertFalse(self.coordinator.snapshot()["pending"])
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
        barrier.wait(); state = self.select(INTERNAL); thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ["acquired"])
        finish_run(auth)
        self.assertEqual(self.coordinator.snapshot()["backend"], INTERNAL)

    def manager(self):
        manager = SkillManager(config_manager=self.config, auto_load=False, load_mcp_tools=False)
        for name in ("workbench", "browser-automation"):
            manager.skill_source_kinds[name] = "core_builtin" if name == "workbench" else TOOL_SOURCE_OPTIONAL
            manager._load_legacy_implementation(name, str(ROOT / ("skills" if name == "workbench" else "ai_skills") / name / "impl.py"))
        return manager

    def test_exposure_search_validation_and_prefix_pinning(self):
        manager = self.manager()
        for backend in (EXTERNAL, INTERNAL):
            self.select(backend)
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
            self.select(EXTERNAL if backend == INTERNAL else INTERNAL)
            after = manager.get_tool_definitions(run_context=context, discovered_tool_names=all_names)
            self.assertEqual(json.dumps(after, sort_keys=True), before)
            # A catalog refresh must retain the pinned run view even after the
            # new inventory was built under the opposite saved preference.
            fresh = self.manager()
            snapshot = type("Snapshot", (), {"revision": "refreshed", "runtime": lambda *_args, **_kwargs: fresh})()
            manager.apply_snapshot(snapshot)
            self.assertEqual(json.dumps(manager.get_tool_definitions(run_context=context, discovered_tool_names=all_names), sort_keys=True), before)

    def test_workers_inherit_authorization_backend_after_saved_toggle(self):
        auth = self.auth()
        self.select(INTERNAL)
        self.assertEqual(bind_run(self.config, auth, worker=True)["backend"], EXTERNAL)
        self.assertEqual(auth.runtime_snapshot()["browser_backend"]["backend"], EXTERNAL)
        finish_run(auth)
        self.assertFalse(auth._browser_state["finished"])
        finish_run(auth)
        self.assertTrue(auth._browser_state["finished"])

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

    def test_not_ready_before_session_start_does_not_block_component_preparation(self):
        spec = importlib.util.spec_from_file_location("not_ready_browser", ROOT / "ai_skills/browser-automation/impl.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        auth = self.auth()
        with patch.object(module, "run_browser_skill_cli", return_value={"status": "incomplete", "executed": False, "error": {"code": "browser_skill_not_ready"}}):
            self.assertEqual(module.browser_skill_cli(["session", "start"], _context={"execution_authorization": auth})["status"], "incomplete")
        with patch("core.browser_skill_component._run_bsk") as cleanup:
            finish_run(auth)
            cleanup.assert_not_called()
        self.assertEqual(self.coordinator.snapshot()["cleanup_errors"], [])
        with self.coordinator.maintenance():
            self.assertEqual(self.coordinator.snapshot()["active_runs"], 0)


if __name__ == "__main__": unittest.main()
