import importlib.util
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.execution_authorization import (
    ApprovalGrant, AuthorizationError, ExecutionAuthorization, bind_authorization,
    current_authorization, invoke, prepare_action, authorization_from_host_snapshot,
)
from core.interaction import InteractionService, parse_interaction_reply
from core.skill_manager import SkillManager
from core.tool_registry import ToolRegistry


def load_files():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills", "file-system", "impl.py")
    spec = importlib.util.spec_from_file_location("authorization_files", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExecutionAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = os.path.join(self.temp.name, "project")
        os.mkdir(self.workspace)
        self.auth = ExecutionAuthorization("session", "run", False, os.path.join(self.temp.name, "artifacts"))
        self.context = {"execution_authorization": self.auth, "workspace_dir": self.workspace,
                        "tool_call_id": "call", "file_state": {"reads": {}}}
        self.files = load_files()

    def manager(self, name, handler):
        manager = SkillManager.__new__(SkillManager)
        manager.workspace_dir = self.workspace
        manager.tools = {name: handler}
        manager.tool_registry = ToolRegistry()
        manager.tool_registry.register(name, handler, "test", {})
        manager.tool_to_skill_map = {}
        manager.skill_records = {}
        manager.change_publisher = None
        return manager

    def approve(self, **kwargs):
        return patch("core.interaction.interaction_service.create_request", return_value={"approved": True, "status": "completed"}, **kwargs)

    def test_missing_or_model_forged_context_does_not_execute(self):
        handler = Mock()
        manager = self.manager("get_everything", handler)
        for context in ({}, {"execution_authorization": {"god_mode": True}}, {"run_context": {"god_mode": True}}):
            result = manager.call_tool("get_everything", {}, context)
            self.assertFalse(result["executed"])
        handler.assert_not_called()

    def test_god_mode_skips_preparation_and_preserves_result(self):
        auth = ExecutionAuthorization("session", "run", True, "")
        prepare = Mock(side_effect=AssertionError("must not prepare"))
        handler = Mock(return_value={"existing": "result"})
        with patch("core.interaction.interaction_service.create_request") as request:
            result = invoke("anything", {}, handler, {"execution_authorization": auth}, handler, preparation=prepare)
        self.assertEqual(result, {"existing": "result"})
        request.assert_not_called()
        handler.assert_called_once()

    def test_read_outside_workspace_without_approval(self):
        path = os.path.join(self.temp.name, "original.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("original")
        manager = self.manager("text_file_read", self.files.text_file_read)
        with patch("core.interaction.interaction_service.create_request") as request:
            result = manager.call_tool("text_file_read", {"path": path}, self.context)
        self.assertTrue(result["ok"], result)
        request.assert_not_called()

    def test_artifact_add_is_free_but_project_add_requires_permission(self):
        manager = self.manager("apply_patch", self.files.apply_patch)
        artifact = os.path.join(self.auth.artifact_root, "report.txt")
        def patch_for(path):
            return {"patch": f"*** Begin Patch\n*** Add File: {path}\n+hello\n*** End Patch"}
        with patch("core.interaction.interaction_service.create_request", return_value={"approved": False, "status": "completed"}) as request:
            result = manager.call_tool("apply_patch", patch_for(artifact), self.context)
            self.assertTrue(result["ok"], result)
            request.assert_not_called()
            denied = manager.call_tool("apply_patch", patch_for("original.txt"), self.context)
            self.assertEqual(denied["authorization_status"], "denied")
            self.assertFalse(os.path.exists(os.path.join(self.workspace, "original.txt")))
            request.assert_called_once()

    def test_approval_precedes_install_and_execution(self):
        events = []
        handler = Mock(side_effect=lambda: events.append("execute"))
        context = {**self.context, "authorization_started": lambda: events.append("started")}
        def approve(*_args, **kwargs):
            events.append("approve")
            return {"approved": True, "status": "completed"}
        with patch("core.interaction.interaction_service.create_request", side_effect=approve):
            invoke("opaque", {}, handler, context, handler)
        self.assertEqual(events, ["approve", "started", "execute"])

    def test_denial_timeout_unavailable_never_start(self):
        for status in ("completed", "timeout", "unavailable", "cancelled"):
            execute, started = Mock(), Mock()
            with patch("core.interaction.interaction_service.create_request", return_value={"approved": False, "status": status}):
                result = invoke("opaque", {}, execute, {**self.context, "authorization_started": started}, execute)
            self.assertFalse(result["executed"])
            execute.assert_not_called()
            started.assert_not_called()

    def test_changed_target_invalidates_approval(self):
        path = os.path.join(self.workspace, "original.txt")
        with open(path, "w") as handle:
            handle.write("before")
        execute = Mock()
        def approve(*_args, **kwargs):
            with open(path, "w") as handle:
                handle.write("changed")
            return {"approved": True, "status": "completed"}
        with patch("core.interaction.interaction_service.create_request", side_effect=approve):
            result = invoke("workspace_delete_path", {"path": path}, self.files.workspace_delete_path, self.context, execute)
        self.assertFalse(result["executed"])
        execute.assert_not_called()

    def test_changed_arguments_and_script_invalidate_approval(self):
        script = os.path.join(self.workspace, "script.py")
        with open(script, "w") as handle:
            handle.write("print(1)")
        execute = Mock()
        args = {"code": "before"}
        def approve(*_args, **kwargs):
            args["code"] = "after"
            with open(script, "w") as handle:
                handle.write("print(2)")
            return {"approved": True, "status": "completed"}
        with patch("core.interaction.interaction_service.create_request", side_effect=approve):
            result = invoke("opaque", args, execute, self.context, execute,
                            preparation=lambda: {"script_paths": [script]})
        self.assertFalse(result["executed"])
        execute.assert_not_called()

    def test_grant_is_single_use_and_bound_to_run_and_call(self):
        action = prepare_action("opaque", {}, Mock(), self.context, self.auth)
        grant = ApprovalGrant(self.auth, action.fingerprint, "call")
        with self.assertRaises(AuthorizationError):
            grant.consume(ExecutionAuthorization("other", "run", False, ""), action, "call")
        with self.assertRaises(AuthorizationError):
            grant.consume(self.auth, action, "other")
        grant.consume(self.auth, action, "call")
        with self.assertRaises(AuthorizationError):
            grant.consume(self.auth, action, "call")

    def test_snapshot_and_child_inheritance_do_not_read_changed_config(self):
        config = SimpleNamespace(get_god_mode=lambda: False, get_chat_workspace_root=lambda: self.temp.name)
        parent = ExecutionAuthorization.start(config, "s")
        config.get_god_mode = lambda: True
        with bind_authorization(parent):
            self.assertIs(current_authorization(), parent)
            self.assertFalse(current_authorization().god_mode)
        self.assertTrue(ExecutionAuthorization.start(config, "s").god_mode)
        parent.cancel()
        with self.assertRaises(AuthorizationError):
            parent.check()

    def test_side_effect_error_is_not_reported_as_not_executed(self):
        execute = Mock(side_effect=OSError("disk failure after start"))
        with self.approve(), self.assertRaises(OSError):
            invoke("opaque", {}, execute, self.context, execute)

    def test_no_receiver_is_immediately_unavailable(self):
        response = InteractionService().create_request("s", "approval", "test", require_receiver=True)
        self.assertEqual(response["status"], "unavailable")

    def test_dependency_installation_is_not_started_when_rejected(self):
        handler = Mock()
        manager = self.manager("opaque", handler)
        manager.tool_to_skill_map = {"opaque": "extension"}
        manager.skill_records = {"extension": {"dependency_status": {"ok": False},
                                              "spec": {"python_dependencies": ["example-package"]}}}
        manager.ensure_skill_dependencies_ready = Mock()
        with patch("core.interaction.interaction_service.create_request", return_value={"status": "completed", "approved": False}) as ask:
            result = manager.call_tool("opaque", {}, self.context)
        self.assertFalse(result["executed"])
        self.assertIn("example-package", ask.call_args.kwargs["metadata"]["details"])
        manager.ensure_skill_dependencies_ready.assert_not_called()
        handler.assert_not_called()

    def test_alias_cannot_evade_gate(self):
        handler = Mock()
        manager = self.manager("opaque", handler)
        manager.tool_registry.alias_to_name["read_safe"] = "opaque"
        with patch("core.interaction.interaction_service.create_request", return_value={"status": "completed", "approved": False}) as ask:
            result = manager.call_tool("read_safe", {}, self.context)
        self.assertFalse(result["executed"])
        self.assertEqual(ask.call_args.kwargs["source_tool"], "opaque")
        handler.assert_not_called()

    def test_approved_python_bypasses_old_ast_filter(self):
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills", "python-runner", "impl.py")
        spec = importlib.util.spec_from_file_location("authorization_python", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        process = Mock()
        process.communicate.return_value = (b"ok", b"")
        process.returncode = 0
        manager = self.manager("run_python_code", module.run_python_code)
        with self.approve(), patch.object(module, "run_in_sandbox", return_value=process), patch.object(module, "get_runtime_executable", return_value="python"):
            result = manager.call_tool("run_python_code", {"code": "import ctypes\nprint('C:/outside')"}, self.context)
        self.assertNotIn("Security Alert", str(result))
        process.communicate.assert_called()

    def test_existing_delete_confirmation_is_preserved_only_when_needed(self):
        for god in (False, True):
            path = os.path.join(self.workspace, "delete.txt")
            with open(path, "w") as handle:
                handle.write("delete")
            auth = ExecutionAuthorization("session", "run", god, self.auth.artifact_root)
            manager = self.manager("workspace_delete_path", self.files.workspace_delete_path)
            with self.approve() as ask, patch.object(self.files, "ask_user", return_value=True) as legacy:
                result = manager.call_tool("workspace_delete_path", {"path": path}, {**self.context, "execution_authorization": auth})
            self.assertTrue(result["ok"], result)
            self.assertEqual(ask.call_count, 0 if god else 1)
            self.assertEqual(legacy.call_count, 1 if god else 0)

    def test_audit_failure_cannot_execute(self):
        execute = Mock()
        with self.approve():
            result = invoke("opaque", {}, execute, {**self.context, "authorization_event": Mock(side_effect=OSError("journal failed"))}, execute)
        self.assertFalse(result["executed"])
        execute.assert_not_called()

    def test_atomic_response_and_cancellation_without_ui(self):
        from PySide6.QtCore import Qt
        service = InteractionService()
        captured = []
        service.interaction_requested.connect(captured.append, Qt.DirectConnection)
        stopped = threading.Event()
        result = []
        thread = threading.Thread(target=lambda: result.append(service.create_request(
            "session", "approval", "confirm", abort_check=stopped.is_set, require_receiver=True)))
        thread.start()
        import time
        deadline = time.monotonic() + 2
        while not captured and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(captured)
        stopped.set()
        self.assertFalse(service.resolve_request(captured[0]["request_id"], True))
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0]["status"], "cancelled")
        self.assertIsNone(service.get_pending_request("session"))

    def test_run_artifact_roots_are_distinct_and_under_workspace(self):
        config = SimpleNamespace(get_god_mode=lambda: False)
        first = ExecutionAuthorization.start(config, "s", workspace_dir=self.workspace)
        second = ExecutionAuthorization.start(config, "s", workspace_dir=self.workspace)
        self.assertNotEqual(first.artifact_root, second.artifact_root)
        self.assertEqual(os.path.commonpath([first.artifact_root, self.workspace]), self.workspace)

    def test_reply_identity_must_match(self):
        _, valid, _ = parse_interaction_reply({"request_id": "one", "kind": "approval"},
                                             {"request_id": "two", "approved": True})
        self.assertFalse(valid)

    def test_code_block_uses_gate_before_execution(self):
        from core.agent import CodeWorker
        from core.runtime_journal import RuntimeJournal
        worker = CodeWorker("print('test')", self.workspace, execution_authorization=self.auth,
                            runtime_journal=RuntimeJournal(os.path.join(self.temp.name, "journal")))
        worker._run_authorized = Mock(return_value={"returncode": 0})
        with patch("core.interaction.interaction_service.create_request", return_value={"status": "completed", "approved": False}):
            worker.run()
        worker._run_authorized.assert_not_called()
        with self.approve():
            worker.run()
        worker._run_authorized.assert_called_once()
        with patch("core.interaction.interaction_service.create_request") as request:
            worker.run()
        worker._run_authorized.assert_called_once()
        request.assert_not_called()

    def test_code_block_unknown_result_prevents_replay(self):
        from core.agent import CodeWorker
        from core.runtime_journal import RuntimeJournal
        journal = RuntimeJournal(os.path.join(self.temp.name, "journal"))
        worker = CodeWorker("pass", self.workspace, execution_authorization=self.auth, runtime_journal=journal)
        worker._run_authorized = Mock(return_value=None)
        with self.approve():
            worker.run()
            worker.run()
        worker._run_authorized.assert_called_once()

    def test_host_snapshot_preserves_policy_and_rejects_wrong_run(self):
        payload = self.auth.runtime_snapshot()
        restored = authorization_from_host_snapshot(payload, session_id=self.auth.session_id, run_id=self.auth.run_id)
        self.assertEqual(restored.artifact_root, self.auth.artifact_root)
        self.assertFalse(restored.god_mode)
        for field, value in (("session_id", "other"), ("run_id", "other"), ("god_mode", "true")):
            with self.assertRaises(ValueError):
                authorization_from_host_snapshot({**payload, field: value}, session_id=self.auth.session_id, run_id=self.auth.run_id)
        self.auth.cancel()
        restored = authorization_from_host_snapshot(self.auth.runtime_snapshot(), session_id=self.auth.session_id, run_id=self.auth.run_id)
        with self.assertRaises(AuthorizationError):
            restored.check()

    def test_nested_directory_change_invalidates_recursive_delete(self):
        directory = os.path.join(self.workspace, "folder")
        os.makedirs(os.path.join(directory, "nested"))
        path = os.path.join(directory, "nested", "value.txt")
        with open(path, "w") as handle:
            handle.write("before")
        execute = Mock()
        def approve(*args, **kwargs):
            with open(path, "w") as handle:
                handle.write("after")
            return {"status": "completed", "approved": True}
        with patch("core.interaction.interaction_service.create_request", side_effect=approve):
            result = invoke("workspace_delete_path", {"path": directory, "recursive": True}, self.files.workspace_delete_path, self.context, execute)
        self.assertEqual(result["authorization_status"], "denied")
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
