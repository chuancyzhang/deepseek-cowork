import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.skill_from_conversation import (
    build_skill_json,
    extract_impl_tool_refs,
    normalize_skill_draft,
    render_session_transcript,
    save_new_skill,
    update_existing_skill_from_draft,
    validate_impl_py,
)
from core.skill_manager import SkillManager


class TestSkillFromConversation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.temp_dir, "skills")
        self.ai_skills_dir = os.path.join(self.temp_dir, "ai_skills")
        os.makedirs(self.skills_dir, exist_ok=True)
        os.makedirs(self.ai_skills_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _manager(self):
        manager = SkillManager(workspace_dir=self.temp_dir)
        manager.skills_dirs = [self.skills_dir, self.ai_skills_dir]
        manager.load_skills()
        return manager

    def _create_existing_skill(self, name="ops-guide"):
        skill_dir = os.path.join(self.ai_skills_dir, name)
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as handle:
            handle.write(
                "---\n"
                f"name: {name}\n"
                "description: Existing operations guide\n"
                "kind: knowledge\n"
                "experience: []\n"
                "---\n\n"
                "# Skill Purpose\nExisting body.\n"
            )
        with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": 2,
                    "name": name,
                    "kind": "knowledge",
                    "description": "Existing operations guide",
                    "tags": ["existing"],
                    "triggers": ["existing trigger"],
                    "tool_refs": [],
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
        return skill_dir

    def test_render_session_transcript_includes_messages_and_tool_calls(self):
        transcript = render_session_transcript(
            "session-1",
            "整理图片",
            [
                {"role": "user", "content": "按日期整理图片"},
                {
                    "role": "assistant",
                    "content": "我会写脚本处理。",
                    "tool_calls": [{"function": {"name": "bash", "arguments": "{}"}}],
                },
            ],
            meta={"workspace_dir": "D:/workspace"},
        )

        self.assertIn("# 会话: 整理图片", transcript)
        self.assertIn("- 工作区: D:/workspace", transcript)
        self.assertIn("按日期整理图片", transcript)
        self.assertIn("[tool_calls]", transcript)
        self.assertIn("bash", transcript)

    def test_normalize_draft_fills_defaults_and_derives_tool_refs(self):
        draft = normalize_skill_draft(
            {
                "skill_name": "Image Sorter!",
                "description": "Sort images by date",
                "impl_py": "def sort_images(path):\n    return path\n",
            }
        )

        self.assertEqual(draft["skill_name"], "image-sorter")
        self.assertEqual(draft["tool_refs"], ["sort_images"])
        self.assertTrue(draft["usage_guidelines"])

    def test_impl_validation_accepts_valid_code_and_rejects_bad_code(self):
        ok, error = validate_impl_py("def hello(name):\n    return name\n")
        self.assertTrue(ok, error)

        ok, error = validate_impl_py("def broken(:\n    pass\n")
        self.assertFalse(ok)
        self.assertIn("syntax error", error)

    def test_save_new_skill_without_impl_creates_knowledge_package(self):
        result = save_new_skill(
            {
                "skill_name": "meeting-notes",
                "description": "Capture meeting note patterns",
                "description_cn": "沉淀会议纪要经验",
                "usage_guidelines": "Use for recurring meeting summaries.",
                "experience_items": ["Keep action items separate."],
                "tags": ["meeting"],
                "triggers": ["meeting notes"],
            },
            target_root=self.ai_skills_dir,
        )

        self.assertTrue(result.ok, result.message)
        self.assertTrue(os.path.exists(os.path.join(result.path, "SKILL.md")))
        self.assertTrue(os.path.exists(os.path.join(result.path, "skill.json")))
        self.assertFalse(os.path.exists(os.path.join(result.path, "impl.py")))
        payload = build_skill_json({"skill_name": "meeting-notes", "description": "x"})
        self.assertEqual(payload["source_format"], "cowork")

    def test_save_new_skill_with_valid_impl_creates_tool_package(self):
        result = save_new_skill(
            {
                "skill_name": "path-helper",
                "description": "Reusable path helper",
                "usage_guidelines": "Use for path formatting.",
                "impl_py": "def normalize_path(path):\n    return path.replace('\\\\', '/')\n",
            },
            target_root=self.ai_skills_dir,
        )

        self.assertTrue(result.ok, result.message)
        impl_path = os.path.join(result.path, "impl.py")
        self.assertTrue(os.path.exists(impl_path))
        with open(os.path.join(result.path, "skill.json"), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["tool_refs"], ["normalize_path"])

    def test_save_new_skill_rejects_invalid_impl_without_writing_package(self):
        result = save_new_skill(
            {
                "skill_name": "bad-helper",
                "description": "Bad helper",
                "impl_py": "def broken(:\n    pass\n",
            },
            target_root=self.ai_skills_dir,
        )

        self.assertFalse(result.ok)
        self.assertFalse(os.path.exists(os.path.join(self.ai_skills_dir, "bad-helper")))

    def test_update_existing_skill_appends_experience_and_merges_metadata(self):
        self._create_existing_skill()
        manager = self._manager()

        result = update_existing_skill_from_draft(
            manager,
            "ops-guide",
            {
                "description": "Updated operations guide",
                "experience_items": ["Capture stderr before retrying."],
                "tags": ["retry"],
                "triggers": ["stderr retry"],
            },
            strategy="append",
        )

        self.assertTrue(result.ok, result.message)
        entries = manager.get_experience_entries("ops-guide")
        self.assertEqual(len(entries), 1)
        self.assertIn("stderr", entries[0]["experience_text"])
        with open(os.path.join(self.ai_skills_dir, "ops-guide", "skill.json"), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertIn("existing", payload["tags"])
        self.assertIn("retry", payload["tags"])
        self.assertIn("stderr retry", payload["triggers"])

    def test_update_existing_skill_rewrite_preserves_structured_entries(self):
        self._create_existing_skill()
        manager = self._manager()
        first = update_existing_skill_from_draft(
            manager,
            "ops-guide",
            {"experience_items": ["Preserve this lesson."]},
            strategy="append",
        )
        self.assertTrue(first.ok, first.message)

        result = update_existing_skill_from_draft(
            manager,
            "ops-guide",
            {
                "description": "Rewritten operations guide",
                "usage_guidelines": "New body guidance.",
                "experience_items": ["New summary lesson."],
            },
            strategy="rewrite",
        )

        self.assertTrue(result.ok, result.message)
        entries = manager.get_experience_entries("ops-guide")
        self.assertEqual(len(entries), 1)
        self.assertIn("Preserve this lesson.", entries[0]["experience_text"])
        with open(os.path.join(self.ai_skills_dir, "ops-guide", "SKILL.md"), "r", encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("Rewritten operations guide", content)
        self.assertIn("New body guidance.", content)


if __name__ == "__main__":
    unittest.main()
