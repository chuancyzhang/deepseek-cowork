import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills.skill_builder import impl as skill_builder_impl


class TestSkillBuilder(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.ai_dir = os.path.join(self.temp_dir, "ai_skills")
        os.makedirs(self.ai_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_and_update_ai_skill(self):
        with patch.object(skill_builder_impl, "get_app_data_dir", return_value=self.temp_dir):
            create_result = skill_builder_impl.create_new_skill(
                workspace_dir=self.temp_dir,
                skill_name="builder-test",
                description="test skill",
                tools_list=[{"name": "hello", "description": "say hello"}],
                tool_code="def hello():\n    return 'ok'\n",
                usage_guidelines="Use it carefully.",
            )
            self.assertIn("Success", create_result)
            update_result = skill_builder_impl.update_skill(
                workspace_dir=self.temp_dir,
                skill_name="builder-test",
                target_scope="ai_only",
                description="updated desc",
                usage_guidelines="Updated usage.",
            )
            self.assertIn("Success", update_result)
            md_path = os.path.join(self.ai_dir, "builder-test", "SKILL.md")
            self.assertTrue(os.path.exists(md_path))
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("description: updated desc", content)
            self.assertIn("Updated usage.", content)

    def test_update_not_found_in_scope(self):
        with patch.object(skill_builder_impl, "get_app_data_dir", return_value=self.temp_dir):
            update_result = skill_builder_impl.update_skill(
                workspace_dir=self.temp_dir,
                skill_name="missing-skill",
                target_scope="ai_only",
                description="x",
            )
            self.assertIn("not found", update_result.lower())


if __name__ == "__main__":
    unittest.main()
