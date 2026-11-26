import json
import os
import sys
import shutil
import tempfile
import unittest
import zipfile
import hashlib
from pathlib import Path
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

    def test_partial_edit_preserves_yaml_resources_and_unrelated_instructions(self):
        with patch.object(skill_builder_impl, "get_app_data_dir", return_value=self.temp_dir):
            result = skill_builder_impl.create_new_skill(self.temp_dir, "partial", "Task scope",
                instructions_md="# Task\n\nOld step.\n\nKeep this section.\n",
                resources=[{"kind": "reference", "path": "references/rules.md", "content": "Keep rules."}],
                _context=self.context)
            self.assertIn("Success", result)
            root = Path(self.ai_dir, "partial")
            original = (root / "SKILL.md").read_text(encoding="utf-8")
            original = original.replace("type: ai_generated", "metadata:\n  author: original\n  nested:\n    keep: yes\ntype: ai_generated")
            (root / "SKILL.md").write_text(original, encoding="utf-8")
            result = skill_builder_impl.update_skill(self.temp_dir, "partial", description="New scope",
                instruction_edits=[{"old_text": "Old step.", "new_text": "New step."}], _context=self.context)
            self.assertIn("Success", result)
            edited = (root / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("metadata:\n  author: original\n  nested:\n    keep: yes", edited)
            self.assertIn("Keep this section.", edited)
            self.assertNotIn("Describe cases", edited)
            self.assertEqual((root / "references/rules.md").read_text(), "Keep rules.")
            result = skill_builder_impl.update_skill(self.temp_dir, "partial",
                instruction_edits=[{"old_text": "Old step.", "new_text": "Wrong stale edit."}], _context=self.context)
            self.assertIn("exactly once", result)
            self.assertEqual((root / "SKILL.md").read_text(encoding="utf-8"), edited)

    def test_template_asset_copy_and_checked_replacement(self):
        source = Path(self.temp_dir, "reference.docx")
        source.write_bytes(b"template reference")
        resource = {"kind": "asset", "path": "assets/reference.docx", "source_path": str(source)}
        with patch.object(skill_builder_impl, "get_app_data_dir", return_value=self.temp_dir):
            result = skill_builder_impl.create_new_skill(self.temp_dir, "template", "Reusable template",
                instructions_md="Use [the reference](assets/reference.docx).", resources=[resource], _context=self.context)
            self.assertIn("Success", result)
            target = Path(self.ai_dir, "template", "assets/reference.docx")
            source.write_bytes(b"new reference")
            result = skill_builder_impl.update_skill(self.temp_dir, "template",
                resources=[{**resource, "previous_sha256": "0" * 64}], _context=self.context)
            self.assertIn("changed since inspection", result)
            self.assertEqual(target.read_bytes(), b"template reference")
            result = skill_builder_impl.update_skill(self.temp_dir, "template", resources=[resource], _context=self.context)
            self.assertIn("Success", result)
            self.assertEqual(target.read_bytes(), b"new reference")
            self.assertEqual(json.loads((target.parent.parent / "skill.json").read_text())["asset_refs"], ["assets/reference.docx"])
            original_validate = skill_builder_impl._validate_staged_skill

            def validate_then_edit(*args, **kwargs):
                original_validate(*args, **kwargs)
                target.write_bytes(b"user edit")

            with patch.object(skill_builder_impl, "_validate_staged_skill", side_effect=validate_then_edit):
                result = skill_builder_impl.update_skill(self.temp_dir, "template", resources=[resource], _context=self.context)
            self.assertIn("changed during update", result)
            self.assertEqual(target.read_bytes(), b"user edit")

    def test_missing_declared_binding_fails_before_publication(self):
        with patch.object(skill_builder_impl, "get_app_data_dir", return_value=self.temp_dir):
            result = skill_builder_impl.create_new_skill(self.temp_dir, "missing-binding", "Task",
                tools_list=[{"name": "hello", "binding": "impl.py:missing"}],
                tool_code="def hello():\n    return 'ok'\n", _context=self.context)
        self.assertIn("Tool binding is missing", result)
        self.assertFalse(Path(self.ai_dir, "missing-binding").exists())
        self.assertEqual(self.events, [])

    def test_invalid_resource_and_missing_link_roll_back_creation(self):
        with patch.object(skill_builder_impl, "get_app_data_dir", return_value=self.temp_dir):
            for resource in ({"kind": "reference", "path": "references/../../escape", "content": "bad"},
                             {"kind": "asset", "path": "assets/missing", "source_path": "missing.docx"}):
                result = skill_builder_impl.create_new_skill(self.temp_dir, "invalid", "Task", resources=[resource], _context=self.context)
                self.assertIn("Error", result)
                self.assertFalse(Path(self.ai_dir, "invalid").exists())
            result = skill_builder_impl.create_new_skill(self.temp_dir, "invalid", "Task",
                instructions_md="Read [rules](references/missing.md).", _context=self.context)
            self.assertIn("Missing", result)
            self.assertFalse(Path(self.ai_dir, "invalid").exists())


if __name__ == "__main__":
    unittest.main()
