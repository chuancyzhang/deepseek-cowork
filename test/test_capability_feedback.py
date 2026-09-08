import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.skill_from_conversation import normalize_skill_draft, is_valid_skill_name, save_new_skill
from core.skill_manager import SkillManager
from core.skillhub import SkillHubCache, preferred_version


class CapabilityFeedbackTests(unittest.TestCase):
    def test_chinese_name_survives_save(self):
        draft = {"skill_name": "客户资料整理", "description": "整理资料", "instructions_md": "按客户整理资料。", "quality": "high"}
        self.assertEqual(normalize_skill_draft(draft)["skill_name"], draft["skill_name"])
        self.assertTrue(is_valid_skill_name(draft["skill_name"]))
        for name in ("../outside", "CON", "a/b", "a" * 129):
            self.assertFalse(is_valid_skill_name(name))
        with tempfile.TemporaryDirectory() as root:
            result = save_new_skill(draft, root)
            self.assertTrue(result.ok, result)
            self.assertTrue((Path(root) / draft["skill_name"] / "SKILL.md").is_file())

    def test_reference_string_is_not_validated_character_by_character(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            (folder / "SKILL.md").write_text("---\nname: stable-id\n---\n正文", encoding="utf-8")
            (folder / "说明.md").write_text("说明", encoding="utf-8")
            (folder / "skill.json").write_text(json.dumps({"references": "说明.md"}), encoding="utf-8")
            manager = SkillManager.__new__(SkillManager)
            with patch.object(manager, "_find_skill_path", return_value=root):
                self.assertTrue(manager.validate_skill("stable-id")["ok"])
                (folder / "skill.json").write_text(json.dumps({"references": "这里不是一个文件路径"}), encoding="utf-8")
                issues = manager.validate_skill("stable-id")["issues"]
                self.assertEqual(len(issues), 1)
                self.assertIn("这里不是一个文件路径", issues[0])

    def test_rename_keeps_identifier_and_body(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "SKILL.md"
            path.write_text("---\nname: stable-id\n---\n正文", encoding="utf-8")
            manager = SkillManager.__new__(SkillManager)
            with patch.object(manager, "_find_skill_path", return_value=root), patch.object(manager, "is_skill_editable", return_value=True):
                result = manager.rename_skill_display_name("stable-id", "中文名称")
                self.assertTrue(result["ok"], result)
                meta, body = manager._parse_skill_md_content(str(path))
                self.assertEqual(meta["name"], "stable-id")
                self.assertEqual(meta["display_name"], "中文名称")
                self.assertEqual(body, "正文")

    def test_published_version_wins_over_history_order(self):
        detail = {"latestVersion": {"version": "1.0.4"}, "versions": [{"version": "2.0.0"}, {"version": "1.0.4"}]}
        self.assertEqual(preferred_version(detail), "1.0.4")
        self.assertEqual(preferred_version(detail, "2.0.0"), "2.0.0")

    def test_refresh_only_removes_catalog_cache(self):
        with tempfile.TemporaryDirectory() as root:
            cache = SkillHubCache(root)
            cache.put(["list"], {"skills": []})
            other = Path(root) / "keep.txt"
            other.write_text("keep")
            cache.clear()
            self.assertIsNone(cache.get(["list"]))
            self.assertTrue(other.exists())
