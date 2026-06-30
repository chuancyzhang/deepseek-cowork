import os
import json
import shutil
import sys
import tempfile
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.skill_adapter import adapt_skill_directory, is_skill_source_dir


class TestSkillAdapter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_detects_standard_agent_skill_source_dir(self):
        source_dir = os.path.join(self.temp_dir, "agent-skill")
        os.makedirs(os.path.join(source_dir, "scripts"), exist_ok=True)
        with open(os.path.join(source_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: agent-skill\ndescription: Agent skill\n---\n")
        self.assertTrue(is_skill_source_dir(source_dir))

    def test_rejects_generic_folder_as_skill_source_dir(self):
        source_dir = os.path.join(self.temp_dir, "generic-folder")
        os.makedirs(source_dir, exist_ok=True)
        with open(os.path.join(source_dir, "README.md"), "w", encoding="utf-8") as f:
            f.write("hello\n")
        self.assertFalse(is_skill_source_dir(source_dir))

    def test_adapt_agent_skill_preserves_original_skill_md_and_resources(self):
        source_dir = os.path.join(self.temp_dir, "aihot")
        os.makedirs(os.path.join(source_dir, "scripts"), exist_ok=True)
        os.makedirs(os.path.join(source_dir, "references"), exist_ok=True)
        original_md = (
            "---\n"
            "name: aihot\n"
            "description: AI HOT 中文 AI 资讯查询 Skill。今天 AI 圈有什么时使用。\n"
            "---\n\n"
            "# AI HOT Skill\n\n"
            "保持原始工作流。\n"
        )
        with open(os.path.join(source_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(original_md)
        with open(os.path.join(source_dir, "scripts", "hello.py"), "w", encoding="utf-8") as f:
            f.write("print('hello')\n")
        with open(os.path.join(source_dir, "references", "api.md"), "w", encoding="utf-8") as f:
            f.write("api notes\n")

        target_dir = os.path.join(self.temp_dir, "installed-aihot")
        result = adapt_skill_directory(source_dir, target_dir)

        self.assertEqual(result["source_format"], "agent_skill")
        with open(os.path.join(target_dir, "SKILL.md"), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), original_md)
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "scripts", "hello.py")))
        self.assertTrue(os.path.isfile(os.path.join(target_dir, "references", "api.md")))
        with open(os.path.join(target_dir, "skill.json"), "r", encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(payload["name"], "aihot")
        self.assertEqual(payload["source_format"], "agent_skill")
        self.assertIn("今天 AI 圈有什么", payload["description"])
        self.assertEqual(payload["prompt_disclosure"], "full_on_match")

    def test_adapt_agent_skill_requires_name_and_description(self):
        source_dir = os.path.join(self.temp_dir, "bad-skill")
        os.makedirs(source_dir, exist_ok=True)
        with open(os.path.join(source_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: bad-skill\n---\n# Missing description\n")

        with self.assertRaisesRegex(ValueError, "description"):
            adapt_skill_directory(source_dir, os.path.join(self.temp_dir, "bad-target"))


if __name__ == "__main__":
    unittest.main()
