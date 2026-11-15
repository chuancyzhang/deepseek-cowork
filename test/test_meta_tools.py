import unittest
import os
import sys
import shutil
import tempfile
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from pathlib import Path

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.skill_manager import SkillManager

class TestMetaTools(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.temp_dir, "skills")
        self.ai_skills_dir = os.path.join(self.temp_dir, "ai_skills")
        os.makedirs(self.skills_dir)
        os.makedirs(self.ai_skills_dir)
        
        # Create a dummy skill to update
        self.skill_name = "test-skill"
        self.skill_path = os.path.join(self.skills_dir, self.skill_name)
        os.makedirs(self.skill_path)
        
        self.skill_md_path = os.path.join(self.skill_path, "SKILL.md")
        with open(self.skill_md_path, "w", encoding='utf-8') as f:
            f.write("---\nname: test-skill\ndescription: A test skill\n---\n# Test Skill\n\nOriginal content.")
            
        # Initialize SkillManager
        self.sm = SkillManager(workspace_dir=self.temp_dir)
        # Force override skills_dirs for testing
        self.sm.skills_dirs = [self.skills_dir, self.ai_skills_dir]
        self.sm.load_skills()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_update_experience(self):
        # 1. Update experience
        new_exp = "Use absolute paths."
        success, msg = self.sm.update_skill_experience(self.skill_name, new_exp)
        self.assertTrue(success, msg)
        
        # 2. Verify file content
        with open(self.skill_md_path, "r", encoding='utf-8') as f:
            content = f.read()
        
        print(f"DEBUG: Content after update:\n{content}")
        self.assertIn("experience: [\"Use absolute paths.\"]", content)
        
        # 3. Add another experience
        success, msg = self.sm.update_skill_experience(self.skill_name, "Another tip.")
        self.assertTrue(success, msg)
        
        with open(self.skill_md_path, "r", encoding='utf-8') as f:
            content = f.read()
            
        print(f"DEBUG: Content after 2nd update:\n{content}")
        self.assertIn("experience: [\"Use absolute paths.\", \"Another tip.\"]", content)

    def test_load_experience_into_prompt(self):
        # 1. Manually write experience to file
        with open(self.skill_md_path, "w", encoding='utf-8') as f:
            f.write("---\nname: test-skill\nexperience: [\"Always check errors.\"]\n---\n# Test Skill\n\nBody content.")
            
        # 2. Reload skills
        self.sm.load_skills()
        
        # 3. Check skill_prompts
        prompt = self.sm.get_full_skill_prompt(self.skill_name) or ""
        found = "Always check errors." in prompt and "Learned Experience" in prompt
        
        self.assertTrue(found, "Experience not injected into skill prompts")

class MemoryExperienceToolTests(unittest.TestCase):
    def setUp(self):
        from core.execution_authorization import ExecutionAuthorization
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.config = SimpleNamespace(get_chat_history_dir=lambda: self.root,
                                      get_projects=lambda: [{"path": self.root}])
        self.context = {"config_manager": self.config, "workspace_dir": self.root,
                        "session_id": "lesson-session", "run_context": {"mode": "execution"},
                        "execution_authorization": ExecutionAuthorization("lesson-session", "run", False, self.root)}
        path = Path(__file__).resolve().parents[1] / "skills/meta-tools/impl.py"
        self.manager = SkillManager(workspace_dir=self.root, auto_load=False, load_mcp_tools=False)
        self.manager._load_legacy_implementation("meta-tools", str(path))
        self.handler = self.manager.tools["update_experience"]

    def call(self, **args):
        return self.manager.call_tool("update_experience", args, self.context)

    def test_autonomous_write_with_god_mode_off_does_not_modify_skills(self):
        from core.memory_store import MemoryStore
        publisher = MagicMock()
        self.context["skill_change_publisher"] = publisher
        with patch("core.interaction.interaction_service.create_request") as approval:
            result = self.call(scope="project", experience="项目中已验证的方法")
        self.assertEqual(result["status"], "ok", result)
        approval.assert_not_called()
        publisher.assert_not_called()
        store = MemoryStore(self.root)
        self.assertIn("项目中已验证的方法", store.read_summary("workspace", self.root))
        self.assertEqual(store.read_summary(), "")
        self.assertFalse((Path(self.root) / "general-experience").exists())

    def test_same_named_extension_does_not_inherit_trust(self):
        from core.execution_authorization import trusted_kind
        def update_experience(scope, experience):
            return "fake"
        self.assertEqual(trusted_kind("update_experience", self.handler), "control")
        self.assertEqual(trusted_kind("update_experience", update_experience), "opaque")

    def test_memory_reads_share_the_bound_project_and_label_scopes(self):
        from core.memory_store import MemoryStore
        path = Path(__file__).resolve().parents[1] / "skills/memory-manager/impl.py"
        self.manager._load_legacy_implementation("memory-manager", str(path))
        store = MemoryStore(self.root)
        store.save_summary("全局事实")
        store.save_summary("原项目事实", "workspace", self.root)
        store.save_summary("其他项目事实", "workspace", self.root + "-other")
        self.context.update(memory_project_dir=self.root, workspace_dir=self.root + "-other")
        result = self.manager.call_tool("read_memories", {}, self.context)
        self.assertIn("# 全局记忆", result)
        self.assertIn("# 当前项目记忆", result)
        self.assertIn("原项目事实", result)
        self.assertNotIn("其他项目事实", result)
        self.context["run_context"]["creation_session"] = True
        result = self.manager.call_tool("read_memories", {}, self.context)
        self.assertIn("未绑定项目", result)
        self.assertNotIn("原项目事实", result)

    def test_grilling_blocks_writes_even_with_god_mode(self):
        from core.execution_authorization import ExecutionAuthorization
        from core.memory_store import MemoryStore
        self.context["run_context"]["mode"] = "grilling"
        self.context["execution_authorization"] = ExecutionAuthorization("lesson-session", "run", True, "")
        self.assertFalse(self.manager.is_tool_allowed("update_experience", "grilling"))
        result = self.call(scope="global", experience="不应写入")
        self.assertEqual(result["status"], "error", result)
        self.assertEqual(MemoryStore(self.root).read_summary(), "")

    def test_creation_and_unregistered_workspace_do_not_fall_back_to_global(self):
        from core.memory_store import MemoryStore
        for creation in (True, False):
            with self.subTest(creation=creation):
                self.context["run_context"]["creation_session"] = creation
                self.config.get_projects = lambda: []
                result = self.call(scope="project", experience="项目经验")
                self.assertEqual(result["status"], "error", result)
                self.assertEqual(MemoryStore(self.root).read_summary(), "")
        self.context["run_context"]["creation_session"] = True
        self.assertEqual(self.call(scope="global", experience="跨项目验证的方法")["status"], "ok")

    def test_project_target_is_bound_to_run_and_old_parameters_are_rejected(self):
        from core.memory_store import MemoryStore
        self.context["memory_project_dir"] = self.root
        self.context["workspace_dir"] = self.root + "-other"
        self.assertEqual(self.call(scope="project", experience="原项目方法")["status"], "ok")
        before = MemoryStore(self.root).read_summary("workspace", self.root)
        result = self.call(scope="project", experience="错误修改", instructions="overwrite skill", skill_name="meta-tools")
        self.assertIn("error", str(result).lower())
        self.assertEqual(MemoryStore(self.root).read_summary("workspace", self.root), before)

    def test_failure_reports_unsaved_and_does_not_refresh_catalog(self):
        publisher = MagicMock()
        self.context["skill_change_publisher"] = publisher
        with patch("core.memory_store.MemoryStore.update_experience", side_effect=OSError("disk unavailable")):
            result = self.call(scope="global", experience="已验证经验")
        self.assertEqual(result["status"], "error")
        self.assertIn("disk unavailable", result["error"])
        publisher.assert_not_called()


if __name__ == "__main__":
    unittest.main()
