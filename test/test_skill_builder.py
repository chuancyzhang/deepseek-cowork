import json
import os
import sys
import shutil
import tempfile
import unittest
import zipfile
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills.skill_builder import impl as skill_builder_impl


class TestSkillBuilder(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.ai_dir = os.path.join(self.temp_dir, "ai_skills")
        os.makedirs(self.ai_dir, exist_ok=True)
        self.events = []
        self.context = {"skill_change_publisher": self.events.append, "session_id": "session-test"}

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
                _context=self.context,
            )
            self.assertIn("Success", create_result)
            update_result = skill_builder_impl.update_skill(
                workspace_dir=self.temp_dir,
                skill_name="builder-test",
                target_scope="ai_only",
                description="updated desc",
                usage_guidelines="Updated usage.",
                _context=self.context,
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
            self.assertIn("\"tools\"", payload)
            self.assertEqual([event["action"] for event in self.events], ["created", "updated"])

    def test_update_not_found_in_scope(self):
        with patch.object(skill_builder_impl, "get_app_data_dir", return_value=self.temp_dir):
            update_result = skill_builder_impl.update_skill(
                workspace_dir=self.temp_dir,
                skill_name="missing-skill",
                target_scope="ai_only",
                description="x",
            )
            self.assertIn("not found", update_result.lower())

    def test_install_agent_skill_preserves_native_skill_md_and_generates_script_metadata(self):
        source_dir = os.path.join(self.temp_dir, "agent-skill-sample")
        os.makedirs(os.path.join(source_dir, "scripts"), exist_ok=True)
        original_md = (
            "---\nname: agent-skill-sample\ndescription: Native agent skill\nkind: knowledge\n---\n"
            "# Native Skill\n\nKeep this body.\n"
        )
        with open(os.path.join(source_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(original_md)
        with open(os.path.join(source_dir, "scripts", "hello.py"), "w", encoding="utf-8") as f:
            f.write("print('hello')\n")

        with patch.object(skill_builder_impl, "get_app_data_dir", return_value=self.temp_dir):
            result = skill_builder_impl.install_agent_skill(source_dir, _context=self.context)
            self.assertIn("Success", result)

        target_dir = os.path.join(self.ai_dir, "agent-skill-sample")
        with open(os.path.join(target_dir, "SKILL.md"), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), original_md)

        with open(os.path.join(target_dir, "skill.json"), "r", encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(payload["creation_hints"]["source_format"], "agent_skill")
        self.assertEqual(payload["script_refs"], [os.path.normpath("scripts\\hello.py")])
        self.assertEqual(payload["script_entries"][0]["runtime"], "python")
        self.assertEqual(payload["script_entries"][0]["name"], "hello")

    def test_install_agent_skill_accepts_single_markdown_file(self):
        md_path = os.path.join(self.temp_dir, "SKILL_aihot.md")
        original_md = (
            "---\n"
            "name: aihot\n"
            "description: AI HOT 中文 AI 资讯查询 Skill。今天 AI 圈有什么时使用。\n"
            "---\n"
            "# AI HOT\n"
        )
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(original_md)

        with patch.object(skill_builder_impl, "get_app_data_dir", return_value=self.temp_dir):
            result = skill_builder_impl.install_agent_skill(md_path, _context=self.context)
            self.assertIn("Success", result)

        target_dir = os.path.join(self.ai_dir, "aihot")
        with open(os.path.join(target_dir, "SKILL.md"), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), original_md)
        self.assertFalse(os.path.exists(os.path.join(self.temp_dir, "SKILL_aihot")))

    def test_install_agent_skill_accepts_zip_package(self):
        source_dir = os.path.join(self.temp_dir, "zip-source", "zip-skill")
        os.makedirs(source_dir, exist_ok=True)
        with open(os.path.join(source_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: zip-skill\ndescription: Zipped agent skill\n---\n# Zip Skill\n")
        zip_path = os.path.join(self.temp_dir, "zip-skill.zip")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(os.path.join(source_dir, "SKILL.md"), arcname="zip-skill/SKILL.md")

        with patch.object(skill_builder_impl, "get_app_data_dir", return_value=self.temp_dir):
            result = skill_builder_impl.install_agent_skill(zip_path, _context=self.context)
            self.assertIn("Success", result)

        self.assertTrue(os.path.isfile(os.path.join(self.ai_dir, "zip-skill", "SKILL.md")))

    def test_old_convert_tools_are_removed(self):
        self.assertFalse(hasattr(skill_builder_impl, "convert_claude_skill"))
        self.assertFalse(hasattr(skill_builder_impl, "convert_openclaw_skill"))
        self.assertFalse(hasattr(skill_builder_impl, "convert_external_skill"))

    def test_create_rolls_back_when_catalog_publish_fails(self):
        context = {"skill_change_publisher": lambda _event: (_ for _ in ()).throw(RuntimeError("reload failed"))}
        with patch.object(skill_builder_impl, "get_app_data_dir", return_value=self.temp_dir):
            result = skill_builder_impl.create_new_skill(
                workspace_dir=self.temp_dir,
                skill_name="rollback-test",
                description="must not remain visible",
                _context=context,
            )
        self.assertIn("reload failed", result)
        self.assertFalse(os.path.exists(os.path.join(self.ai_dir, "rollback-test")))


if __name__ == "__main__":
    unittest.main()
