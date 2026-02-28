import unittest
import os
import sys
import shutil
import tempfile
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.skill_manager import SkillManager

class TestMetaTools(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.temp_dir, "skills")
        os.makedirs(self.skills_dir)
        
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
        self.sm.skills_dirs = [self.skills_dir]
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

if __name__ == "__main__":
    unittest.main()
