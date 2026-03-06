import unittest
import os
import sys
import shutil
import tempfile
from unittest.mock import MagicMock
import importlib.util

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.skill_manager import SkillManager

_meta_tools_spec = importlib.util.spec_from_file_location(
    "meta_tools_impl",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "meta-tools", "impl.py"),
)
_meta_tools_module = importlib.util.module_from_spec(_meta_tools_spec)
_meta_tools_spec.loader.exec_module(_meta_tools_module)

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

    def test_delete_ai_skill_and_list(self):
        ai_skill = "ai-demo"
        ai_skill_path = os.path.join(self.ai_skills_dir, ai_skill)
        os.makedirs(ai_skill_path)
        with open(os.path.join(ai_skill_path, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: ai-demo\ndescription: ai generated\n---\n# ai")
        with open(os.path.join(ai_skill_path, "impl.py"), "w", encoding="utf-8") as f:
            f.write("def ai_demo_tool():\n    return 'ok'")
        self.sm.load_skills()
        all_skills = self.sm.get_all_skills()
        ai_names = [s.get("name") for s in all_skills if s.get("type") == "ai_generated"]
        self.assertIn("ai-demo", ai_names)
        msg = _meta_tools_module.delete_ai_skill(ai_skill, _context={"skill_manager": self.sm})
        self.assertIn("deleted successfully", msg)
        self.assertFalse(os.path.exists(ai_skill_path))

if __name__ == "__main__":
    unittest.main()
