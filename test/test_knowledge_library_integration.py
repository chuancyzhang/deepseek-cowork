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
                                       context={"run_context": {"knowledge_context": self.scope}})
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

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI integration")
    def test_real_windows_dpapi_roundtrip(self):
        protector = WindowsDpapiProtector()
        value = b"knowledge-validation-token"
        encrypted = protector.protect(value)
        self.assertNotIn(value, encrypted)
        self.assertEqual(protector.unprotect(encrypted), value)


if __name__ == "__main__":
    unittest.main()
