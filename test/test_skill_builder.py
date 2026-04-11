import json
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
            skill_json_path = os.path.join(self.ai_dir, "builder-test", "skill.json")
            self.assertTrue(os.path.exists(md_path))
            self.assertTrue(os.path.exists(skill_json_path))
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("description: updated desc", content)
            self.assertIn("Updated usage.", content)
            with open(skill_json_path, "r", encoding="utf-8") as f:
                payload = f.read()
            self.assertIn("\"version\": 2", payload)

    def test_update_not_found_in_scope(self):
        with patch.object(skill_builder_impl, "get_app_data_dir", return_value=self.temp_dir):
            update_result = skill_builder_impl.update_skill(
                workspace_dir=self.temp_dir,
                skill_name="missing-skill",
                target_scope="ai_only",
                description="x",
            )
            self.assertIn("not found", update_result.lower())

    def test_convert_openclaw_skill_adapts_to_cowork_format(self):
        source_dir = os.path.join(self.temp_dir, "openclaw-sample")
        os.makedirs(os.path.join(source_dir, "prompts"), exist_ok=True)
        with open(os.path.join(source_dir, "openclaw.json"), "w", encoding="utf-8") as f:
            f.write("{\"name\": \"openclaw-sample\"}")
        with open(os.path.join(source_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("# Original OpenClaw Skill\n\nExternal instructions.\n")

        with patch.object(skill_builder_impl, "get_app_data_dir", return_value=self.temp_dir):
            result = skill_builder_impl.convert_openclaw_skill(source_dir, skill_name="openclaw-sample")
            self.assertIn("Success", result)

        target_dir = os.path.join(self.ai_dir, "openclaw-sample")
        self.assertTrue(os.path.exists(os.path.join(target_dir, "skill.json")))
        self.assertTrue(os.path.exists(os.path.join(target_dir, "SKILL.md")))
        self.assertTrue(os.path.exists(os.path.join(target_dir, "references", "source-SKILL.md")))

        with open(os.path.join(target_dir, "skill.json"), "r", encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(payload["creation_hints"]["source_format"], "openclaw")
        self.assertEqual(payload["tool_refs"], [])

        with open(os.path.join(target_dir, "SKILL.md"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Cowork skill system", content)

    def test_convert_agent_skill_preserves_native_skill_md_and_generates_script_metadata(self):
        source_dir = os.path.join(self.temp_dir, "agent-skill-sample")
        os.makedirs(os.path.join(source_dir, "scripts"), exist_ok=True)
        with open(os.path.join(source_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: agent-skill-sample\ndescription: Native agent skill\nkind: knowledge\n---\n"
                "# Native Skill\n\nKeep this body.\n"
            )
        with open(os.path.join(source_dir, "scripts", "hello.py"), "w", encoding="utf-8") as f:
            f.write("print('hello')\n")

        with patch.object(skill_builder_impl, "get_app_data_dir", return_value=self.temp_dir):
            result = skill_builder_impl.convert_external_skill(source_dir, skill_name="agent-skill-sample", source_format="auto")
            self.assertIn("Success", result)

        target_dir = os.path.join(self.ai_dir, "agent-skill-sample")
        with open(os.path.join(target_dir, "SKILL.md"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("# Native Skill", content)
        self.assertIn("Keep this body.", content)

        with open(os.path.join(target_dir, "skill.json"), "r", encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(payload["creation_hints"]["source_format"], "agent_skill")
        self.assertEqual(payload["script_refs"], [os.path.normpath("scripts\\hello.py")])
        self.assertEqual(payload["script_entries"][0]["runtime"], "python")
        self.assertEqual(payload["script_entries"][0]["name"], "hello")


if __name__ == "__main__":
    unittest.main()
