"""Desktop contracts: durable binding, account isolation and fail-closed calls."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import httpx

from core.chat_storage import ChatStorage
from core.clarify_mode import normalize_run_context
from core.enterprise_client import EnterpriseStore
from core.llm.factory import LLMFactory
from core.team_space_storage import TeamSpaceError, TeamSpacePaused, TeamSpaceStore
from core.team_spaces import TeamSpaceClient, bind_space_provider, resolve_run_binding, task_context_message


class Protector:
    def protect(self, data):
        return data
    def unprotect(self, data):
        return data


class TeamSpaceClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.identity = {"platform_id": "company", "member_id": "member"}
        self.enterprise = EnterpriseStore(self.root, protector=Protector())
        self.state = {"connection": {"platform_id": "company", "platform_url": "http://localhost", "platform_name": "企业"},
            "member": {"id": "member"}, "generation": "generation", "client_id": "client01234567890",
            "expires_at": time.time() + 3600, "models": []}
        self.enterprise.write(self.state, "session-token")
        self.local = TeamSpaceStore(self.root)
        self.storage = ChatStorage(str(self.root / "history" / "chat_history.sqlite"))
        self.binding = self.local.bind_workspace(self.identity, "space", self.root / "work")
        self.storage.upsert_conversation("conversation", title="空间任务", meta={"workspace_dir": self.binding["workspace_dir"]})
        self.storage.bind_team_space("conversation", self.binding)
        self.requests, self.offline, self.denied = [], False, False
        def center(request):
            self.requests.append(request)
            if self.offline:
                raise httpx.ConnectError("offline", request=request)
            if request.url.path.endswith("platform-info"):
                return httpx.Response(200, json={"platform_id": "company", "team_spaces": {"version": 1}})
            if request.url.path.endswith("model-policy"):
                return httpx.Response(403, json={"error": {"code": "space_model_forbidden", "message": "空间已撤权"}}) if self.denied else httpx.Response(200, json={"allowed": True})
            return httpx.Response(200, json={"items": []})
        self.client = TeamSpaceClient(self.enterprise, self.local, transport=httpx.MockTransport(center))

    def test_bindings_survive_normal_save_and_meta_updates(self):
        self.storage.upsert_conversation("conversation", title="已改标题", meta={"selected_model_id": "personal"})
        self.storage.update_conversation_meta("conversation", {"pinned": True})
        self.assertEqual(self.storage.get_team_space_binding("conversation"), self.binding)
        with self.assertRaises(TeamSpaceError):
            self.storage.update_conversation_meta("conversation", {"team_space_required": None})
        with self.assertRaises(TeamSpaceError):
            self.storage.bind_team_space("conversation", {**self.binding, "space_id": "other"})

    def test_missing_or_corrupt_binding_never_becomes_personal(self):
        with self.storage._connect() as db:
            db.execute("DELETE FROM conversation_team_spaces WHERE conversation_id='conversation'")
        with self.assertRaises(TeamSpaceError):
            resolve_run_binding(self.storage, "conversation", {})
        self.storage.upsert_conversation("ordinary", meta={})
        self.assertIsNone(resolve_run_binding(self.storage, "ordinary", {}))
        self.storage.upsert_conversation("damaged", meta={"workspace_source": "team_space"})
        with self.assertRaises(TeamSpaceError):
            resolve_run_binding(self.storage, "damaged", {})
        with self.assertRaises(TeamSpaceError):
            resolve_run_binding(self.storage, "ordinary", {"team_space": self.binding})
        with self.assertRaises(TeamSpaceError):
            resolve_run_binding(self.storage, "ordinary", normalize_run_context({"team_space": "damaged"}))

    def test_existing_conversation_cannot_be_imported(self):
        self.storage.upsert_conversation("old", meta={})
        self.storage.replace_messages("old", [{"role": "user", "content": "旧消息"}])
        with self.assertRaises(TeamSpaceError):
            self.storage.bind_team_space("old", self.binding)

    def test_corrupt_binding_finishes_worker_and_preserves_saved_input(self):
        from core.agent import LLMWorker
        from core.execution_authorization import ExecutionAuthorization
        from test_agent_bootstrap import ConfigStub, SkillStub
        messages = [{"id": "input", "role": "user", "content": "保留这次任务输入"}]
        self.storage.replace_messages("conversation", messages)
        with self.storage._connect() as db:
            db.execute("DELETE FROM conversation_team_spaces WHERE conversation_id='conversation'")
        results = []
        profile = {"provider": "openai", "api_key": "test", "model_name": "test"}
        with patch("core.agent.SkillManager", return_value=SkillStub()), \
             patch("core.agent.LLMFactory.create_provider") as factory, \
             patch.object(LLMWorker, "_bind_agent_manager"), \
             patch.object(LLMWorker, "_build_stable_system_prompt", return_value="Stable prompt"), \
             patch("core.agent.get_runtime_snapshot", return_value={}):
            worker = LLMWorker(messages, ConfigStub(str(self.root / "history"), profile),
                workspace_dir=self.binding["workspace_dir"], session_id="conversation", turn_id="turn", request_id="run",
                run_context={"team_space": {"version": 0}},
                execution_authorization=ExecutionAuthorization("conversation", "run", True, ""))
            worker.finished_signal.connect(results.append)
            worker.run()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["error_type"], "TeamSpaceError")
        factory.assert_not_called()
        self.assertEqual(self.storage.get_messages("conversation")[0]["content"], "保留这次任务输入")

    def test_workspaces_reject_projects_nested_paths_and_other_accounts(self):
        project = self.root / "project"
        project.mkdir()
        for directory in (project, project / "child", self.root):
            with self.assertRaises(TeamSpaceError):
                self.local.bind_workspace(self.identity, "another", directory, [project])
        with self.assertRaises(TeamSpaceError):
            self.local.bind_workspace({**self.identity, "member_id": "other"}, "space", self.root / "work")
        self.assertEqual(self.local.bind_workspace(self.identity, "space", self.root / "work"), self.binding)

    def test_nonempty_directory_is_not_silently_imported(self):
        directory = self.root / "existing"
        directory.mkdir()
        (directory / "AGENTS.md").write_text("existing", encoding="utf-8")
        with self.assertRaises(TeamSpaceError):
            self.local.bind_workspace(self.identity, "other", directory)
        self.assertEqual((directory / "AGENTS.md").read_text(), "existing")

    def test_drafts_and_operations_are_account_scoped(self):
        other = {**self.identity, "member_id": "other"}
        self.local.draft(self.identity, "space", "plan", {"title": "private draft"})
        self.assertIsNone(self.local.draft(other, "space", "plan"))
        self.local.stage_operation(self.identity, "operation", "plan.create", "space", {"title": "one"})
        with self.assertRaises(TeamSpaceError):
            self.local.stage_operation(self.identity, "operation", "plan.create", "space", {"title": "two"})
        self.local.stage_operation(other, "operation", "plan.create", "space", {"title": "two"})

    def test_logout_relogin_same_account_requires_new_explicit_run(self):
        self.client.policy("space", {"id": "personal"})
        self.enterprise.write({**self.state, "generation": "new-generation"}, "new-token")
        with self.assertRaises(TeamSpacePaused):
            self.client.policy("space", {"id": "personal"})
        resumed = TeamSpaceClient(self.enterprise, self.local, transport=self.client.transport)
        self.assertTrue(resumed.policy("space", {"id": "personal"})["allowed"])

    def test_late_response_after_account_switch_is_rejected(self):
        def change_account(request):
            self.enterprise.write({**self.state, "member": {"id": "other"}}, "other-token")
            return httpx.Response(200, json={"items": [{"private": "old identity"}]})
        client = TeamSpaceClient(self.enterprise, self.local, transport=httpx.MockTransport(change_account))
        with self.assertRaises(TeamSpacePaused):
            client.request("GET", "/team-spaces")

    def test_each_protocol_checks_center_before_supplier_call(self):
        for protocol, provider in (("chat_completions", "openai"), ("responses", "openai"), ("anthropic", "anthropic")):
            with self.subTest(protocol=protocol):
                profile = {"provider": provider, "api_key": "private-key", "base_url": "https://supplier.invalid/v1",
                           "model_name": "test-model", "api_protocol": protocol}
                instance = LLMFactory.create_provider_from_profile(profile)
                original_client = instance.client
                instance.client = Mock()
                sdk_mock = instance.client
                bind_space_provider(instance, self.binding, profile, client=self.client)
                sdk_mock.with_options.assert_called_once_with(max_retries=0)
                self.offline = True
                before = len(self.requests)
                result = list(instance.chat_stream([{"role": "user", "content": "private input"}]))
                self.assertTrue(any(c.get("error_type") == "TeamSpacePaused" for c in result), result)
                self.assertEqual(instance.client.mock_calls, [])
                self.assertEqual(len(self.requests) - before, 1, "center outage must not auto-retry")
                original_client.close()
                self.offline = False

    def test_policy_checks_send_only_authorization_metadata(self):
        self.client.policy("space", {"id": "personal", "api_key": "private-secret"})
        content = json.loads(self.requests[-1].content)
        self.assertEqual(content, {"source": "personal", "model_id": ""})
        self.denied = True
        with self.assertRaises(TeamSpaceError):
            self.client.policy("space", {"id": "personal"})

    def test_context_is_append_only_task_input_and_not_question_collection(self):
        binding = {**self.binding, "task_context": {"description_revision": 2, "description": "共享背景"}}
        original = [{"role": "system", "content": "stable"}, {"role": "user", "content": "用户问题"}]
        message = task_context_message(binding, original)
        self.assertEqual(message["role"], "user")
        self.assertTrue(message["meta"]["exclude_from_enterprise_questions"])
        self.assertNotIn("enterprise_submission", message["meta"])
        self.assertIsNone(task_context_message(binding, original + [message]))
        self.assertEqual(original[0]["content"], "stable")

    def test_internal_skill_agent_cannot_bypass_space_policy(self):
        from core.remote_skill_installer import RemoteSkillInstallerAgentRunner
        profile = {"provider": "openai", "api_key": "private-key", "base_url": "https://supplier.invalid/v1",
                   "model_name": "test-model", "api_protocol": "chat_completions"}
        provider = LLMFactory.create_provider_from_profile(profile)
        sdk = provider.client
        provider.client = Mock()
        with patch("core.remote_skill_installer.LLMFactory.create_provider", return_value=provider):
            runner = RemoteSkillInstallerAgentRunner(Mock(), profile, context={"chat_storage": self.storage,
                "conversation_id": "conversation", "run_context": {}, "team_space_client": self.client})
        self.denied = True
        result = list(runner.provider.chat_stream([{"role": "user", "content": "private"}]))
        self.assertTrue(any(c.get("error_type") == "TeamSpaceError" for c in result))
        self.assertEqual(runner.provider.client.mock_calls, [])
        sdk.close()


if __name__ == "__main__":
    unittest.main()
