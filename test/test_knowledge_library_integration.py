import copy
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.agent import LLMWorker
from core.knowledge_library import KnowledgeService, KnowledgeStore, knowledge_context_message
from core.message_persistence import filter_persistable_messages
from core.conversation_render import _is_hidden_context_message
from core.skill_manager import SkillManager
from core.variable_store import WindowsDpapiProtector
from core.execution_authorization import ExecutionAuthorization
from test_knowledge_library import TestProtector, WeKnoraFixture


class KnowledgeIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = KnowledgeService(KnowledgeStore(self.temp.name, TestProtector()), WeKnoraFixture())
        self.service.login("http://localhost", "reader@example.test", "password")
        self.scope = self.service.snapshot(session_id="session-a")

    def manager(self, with_knowledge=True):
        root = os.path.dirname(os.path.dirname(__file__))
        skills = os.path.join(self.temp.name, "skills")
        optional = os.path.join(self.temp.name, "ai_skills")
        if not os.path.exists(skills):
            os.makedirs(skills)
            os.makedirs(optional)
            shutil.copytree(os.path.join(root, "skills", "command-tools"), os.path.join(skills, "command-tools"))
        if with_knowledge and not os.path.exists(os.path.join(optional, "knowledge-library")):
            shutil.copytree(os.path.join(root, "ai_skills", "knowledge-library"), os.path.join(optional, "knowledge-library"))
        manager = SkillManager(workspace_dir=self.temp.name, auto_load=False, load_mcp_tools=False)
        manager.skills_dirs = []
        manager._register_skill_root(skills, "core_builtin")
        manager._register_skill_root(optional, "optional")
        manager.load_skills()
        return manager

    def test_knowledge_skill_does_not_add_or_change_tool_schema(self):
        before = self.manager(False).get_tool_definitions(run_mode="execution", run_context={})
        after = self.manager(True).get_tool_definitions(run_mode="execution", run_context={"knowledge_context": self.scope})
        self.assertEqual(before, after)
        names = [item["function"]["name"] for item in after]
        self.assertIn("run_skill_script", names)
        self.assertNotIn("knowledge_search", names)

    def test_actual_script_dispatch_uses_host_and_never_starts_subprocess(self):
        manager = self.manager()
        with patch("core.knowledge_library.KnowledgeService", return_value=self.service), patch("core.sandbox_runtime.run_skill_script_in_sandbox", side_effect=AssertionError("No subprocess allowed")):
            result = manager.call_tool("run_skill_script", {"skill_name": "knowledge-library", "script_name": "search", "input_text": '{"query":"权限"}'},
                                       context={"run_context": {"knowledge_context": self.scope},
                                                "execution_authorization": ExecutionAuthorization("session-a", "test", True, "")})
        self.assertIsInstance(result, dict, result)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["results"][0]["knowledge_id"], "doc-a")
        serialized = json.dumps(result)
        self.assertNotIn("secret-token", serialized)
        self.assertNotIn("secret-refresh", serialized)

    def test_old_mcp_is_blocked_for_knowledge_run(self):
        manager = self.manager()
        manager.skill_records["mcp_weknora"] = {"spec": {"source_skill": "weknora"}}
        self.assertTrue(manager._is_skill_allowed_by_scope("mcp_weknora", {}))
        self.assertFalse(manager._is_skill_allowed_by_scope("mcp_weknora", {"knowledge_context": self.scope}))
        self.assertFalse(manager._is_skill_allowed_by_scope("weknora", {"knowledge_context": self.scope}))

    def test_cloud_skills_aliases_and_credentials_cannot_bypass_scope(self):
        manager = self.manager()
        context = {"knowledge_context": self.scope, "app_variable_access": True}
        for source in ("tencent-docs", "lexiang", "lexiang-mcp-skill"):
            manager.skill_records["alias"] = {"spec": {"source_skill": source}}
            self.assertFalse(manager._is_skill_allowed_by_scope(source, context))
            self.assertFalse(manager._is_skill_allowed_by_scope("alias", context))
        manager.skill_records["alias"] = {"mcp_server": {"url": "https://docs.qq.com/openapi/mcp"}}
        self.assertFalse(manager._is_skill_allowed_by_scope("alias", context))
        manager.skill_records["tencent-docs"] = {"spec": {"name": "tencent-docs"}}
        self.assertTrue(manager._is_skill_allowed_by_scope("knowledge-library", {**context, "allowed_skill_names": ["tencent-docs"]}))
        result = manager._lookup_app_variable("LEXIANG_TOKEN", {"run_context": context})
        self.assertEqual(result["status"], "denied")

    def test_multisource_context_is_append_only_without_identity_or_credentials(self):
        from core.knowledge_sources import MultiSourceKnowledgeService
        service = MultiSourceKnowledgeService(self.service.store, {"weknora": self.service})
        scope = service.snapshot(requested_source="weknora")
        before = [{"role": "user", "content": "原问题"}]
        messages = copy.deepcopy(before)
        messages.append(knowledge_context_message(scope, "multi"))
        self.assertEqual(messages[:1], before)
        self.assertEqual(filter_persistable_messages(messages), messages)
        self.assertNotIn(self.scope["connection_id"], messages[-1]["content"])
        worker = LLMWorker.__new__(LLMWorker)
        worker.workspace_dir, worker.config_manager = self.temp.name, None
        worker.run_context = {}
        prompt = worker._build_stable_system_prompt()
        worker.run_context = {"knowledge_context": scope}
        self.assertEqual(prompt, worker._build_stable_system_prompt())
        self.assertEqual(self.manager(False).get_tool_definitions(run_mode="execution", run_context={}),
                         self.manager().get_tool_definitions(run_mode="execution", run_context=worker.run_context))

    def test_system_prompt_is_identical_with_knowledge_scope(self):
        worker = LLMWorker.__new__(LLMWorker)
        worker.workspace_dir = self.temp.name
        worker.config_manager = None
        worker.run_context = {"mode": "execution"}
        before = worker._build_stable_system_prompt()
        worker.run_context["knowledge_context"] = self.scope
        self.assertEqual(before, worker._build_stable_system_prompt())

    def test_reference_context_is_append_only_persisted_and_hidden_from_chat(self):
        original = [{"role": "user", "content": "原问题"}, {"role": "assistant", "content": "已有回答"}]
        messages = copy.deepcopy(original)
        context = knowledge_context_message(self.scope, "request-a")
        messages.append(context)
        self.assertEqual(messages[:2], original)
        self.assertEqual(filter_persistable_messages(messages), messages)
        self.assertTrue(_is_hidden_context_message(context))
        self.assertIsNone(knowledge_context_message(None, "request-b"))
        self.assertNotIn(self.scope["generation"], context["content"])

    def test_context_uses_ledger_identity_and_can_merge_on_success_or_error(self):
        from core.conversation_integrity import merge_messages_by_id
        worker = LLMWorker.__new__(LLMWorker)
        worker.turn_id, worker.request_id = "turn-a", "request-a"
        original = [{"id": "user-a", "role": "user", "content": "看看这篇资料"}]
        current, generated = copy.deepcopy(original), []
        worker._append_ledger_message(current, generated, knowledge_context_message(self.scope, "request-a"))
        self.assertTrue(generated[0]["id"])
        self.assertEqual(generated[0]["meta"]["turn_id"], "turn-a")
        merged = merge_messages_by_id(original, generated)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merge_messages_by_id(merged, generated), merged)
        self.assertEqual(original[0]["content"], "看看这篇资料")

    def test_history_restore_keeps_knowledge_context_hidden_and_one_answer(self):
        from main import MainWindow
        from types import SimpleNamespace, MethodType
        from unittest.mock import Mock, MagicMock
        from core.conversation_render import build_conversation_render_spans
        context = knowledge_context_message({"sources": {"weknora": self.scope},
                                             "default_source": "weknora"}, "request-a")
        context["id"] = "context-a"
        final_meta = {"ui_turn_group_id": "group-a", "ui_stage_id": "final-a", "ui_reply_kind": "final"}
        messages = [
            {"id": "user-a", "role": "user", "content": "看看这篇资料"},
            {"id": "live-a", "role": "assistant", "content": "能看到，正文已读取。", "meta": {
                **final_meta, "ui_visible_fragment": True, "ui_source_message_id": "answer-a"}},
            context,
            {"id": "answer-a", "role": "assistant", "content": "能看到，正文已读取。", "meta": final_meta},
        ]
        state = SimpleNamespace(session_id="history-a", messages=json.loads(json.dumps(messages)),
                                ui_timeline_events=[], chat_layout=MagicMock())
        state.chat_layout.count.return_value = 0
        # Exercise the real history routing; stub only widget construction.
        host = SimpleNamespace(render_message_batch=Mock(return_value=1),
                               _render_history_assistant_summary=Mock(return_value=1),
                               _message_is_office_draft_request=lambda _: False,
                               _register_render_node=Mock(), get_session=lambda _: state,
                               _submit_session_request=Mock(side_effect=AssertionError("History must not submit")))
        host._render_projected_history_summary = MethodType(MainWindow._render_projected_history_summary, host)
        for _ in range(2):
            host.render_message_batch.reset_mock()
            host._render_history_assistant_summary.reset_mock()
            for span in build_conversation_render_spans(state.messages):
                MainWindow._render_history_span(host, state, span)
            host.render_message_batch.assert_called_once()
            self.assertEqual(host.render_message_batch.call_args.args[0], [messages[0]])
            host._render_history_assistant_summary.assert_called_once()
            summary = host._render_history_assistant_summary.call_args.args[1]
            self.assertEqual(len(summary), 1)
            self.assertEqual(summary[0]["content"], "能看到，正文已读取。")
            self.assertEqual(state.messages, messages)
        self.assertEqual(MainWindow.render_message_batch(host, [context], state.session_id), 0)
        host._submit_session_request.assert_not_called()

    def test_worker_result_merges_with_knowledge_context(self):
        from core.conversation_integrity import merge_messages_by_id
        from test_plan_mode import _ConfigStub
        class Provider:
            provider_name, model_name, base_url, thinking_enabled = "stub", "stub", "", False
            def chat_stream(self, messages, tools=None):
                yield {"type": "content", "content": "已阅读"}
        manager = self.manager()
        original = [{"id": "user-a", "role": "user", "content": "看看资料"}]
        results = []
        with patch("core.agent.SkillManager", return_value=manager), patch("core.agent.LLMFactory.create_provider", return_value=Provider()):
            worker = LLMWorker(original, _ConfigStub(self.temp.name), workspace_dir=self.temp.name,
                               run_context={"knowledge_context": self.scope},
                               execution_authorization=ExecutionAuthorization("session-a", "test", True, ""))
            worker.finished_signal.connect(results.append)
            worker.run()
        self.assertTrue(results)
        self.assertNotIn("error", results[-1])
        generated = results[-1]["generated_messages"]
        self.assertTrue(any(m.get("meta", {}).get("source") == "knowledge_submission" for m in generated))
        self.assertTrue(all(m.get("id") for m in generated))
        merged = merge_messages_by_id(original, generated)
        self.assertEqual(merged[0], original[0])

    def test_batch_reference_creates_one_conversation_and_saves_once(self):
        from main import MainWindow
        from types import SimpleNamespace
        from unittest.mock import Mock
        state = SimpleNamespace(session_id="batch-session", knowledge_refs=[])
        host = SimpleNamespace(new_conversation=Mock(), get_session=lambda: state,
                               save_chat_history=Mock(), show_conversation_page=Mock(),
                               refresh_context_badges=Mock(), add_system_toast=Mock())
        refs = [self.service.reference(self.scope, "kb-a", "甲", "doc-a"),
                self.service.reference(self.scope, "kb-a", "乙", "doc-b")]
        MainWindow.use_knowledge_reference(host, refs, "new")
        host.new_conversation.assert_called_once()
        host.save_chat_history.assert_called_once_with(session_id="batch-session")
        self.assertEqual(state.knowledge_refs, refs)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI integration")
    def test_real_windows_dpapi_roundtrip(self):
        protector = WindowsDpapiProtector()
        value = b"knowledge-validation-token"
        encrypted = protector.protect(value)
        self.assertNotIn(value, encrypted)
        self.assertEqual(protector.unprotect(encrypted), value)


if __name__ == "__main__":
    unittest.main()
