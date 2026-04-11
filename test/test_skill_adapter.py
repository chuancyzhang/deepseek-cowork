import os
import shutil
import sys
import tempfile
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.skill_adapter import detect_external_skill_format


class TestSkillAdapter(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_detects_cowork_skill(self):
        source_dir = os.path.join(self.temp_dir, "cowork-skill")
        os.makedirs(source_dir, exist_ok=True)
        with open(os.path.join(source_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: cowork-skill\n---\n")
        with open(os.path.join(source_dir, "skill.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        self.assertEqual(detect_external_skill_format(source_dir), "cowork")

    def test_detects_agent_skill(self):
        source_dir = os.path.join(self.temp_dir, "agent-skill")
        os.makedirs(os.path.join(source_dir, "scripts"), exist_ok=True)
        with open(os.path.join(source_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: agent-skill\n---\n")
        self.assertEqual(detect_external_skill_format(source_dir), "agent_skill")

    def test_detects_openclaw(self):
        source_dir = os.path.join(self.temp_dir, "openclaw-skill")
        os.makedirs(os.path.join(source_dir, "prompts"), exist_ok=True)
        with open(os.path.join(source_dir, "openclaw.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        self.assertEqual(detect_external_skill_format(source_dir), "openclaw")

    def test_detects_generic_folder(self):
        source_dir = os.path.join(self.temp_dir, "generic-folder")
        os.makedirs(source_dir, exist_ok=True)
        with open(os.path.join(source_dir, "README.md"), "w", encoding="utf-8") as f:
            f.write("hello\n")
        self.assertEqual(detect_external_skill_format(source_dir), "generic")


if __name__ == "__main__":
    unittest.main()
