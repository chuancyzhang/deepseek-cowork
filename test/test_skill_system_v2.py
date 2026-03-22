import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.skill_manager import SkillManager


class TestSkillSystemV2(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.temp_dir, "skills")
        self.ai_skills_dir = os.path.join(self.temp_dir, "ai_skills")
        os.makedirs(self.skills_dir, exist_ok=True)
        os.makedirs(self.ai_skills_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _build_manager(self):
        sm = SkillManager(workspace_dir=self.temp_dir)
        sm.skills_dirs = [self.skills_dir, self.ai_skills_dir]
        sm.load_skills()
        return sm

    def test_knowledge_skill_is_discoverable_without_registering_new_tools(self):
        skill_dir = os.path.join(self.skills_dir, "http-guide")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: http-guide\ndescription: HTTP API interface notes\nkind: knowledge\n---\n"
                "# Skill Purpose\nUse this skill for API headers and retries.\n"
            )
        with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 2,
                    "name": "http-guide",
                    "kind": "knowledge",
                    "description": "HTTP API interface notes",
                    "tags": ["http", "api", "headers"],
                    "triggers": ["api retry", "authorization header"],
                    "tool_refs": ["bash"],
                    "workflow": ["Read the API notes before using lightweight tools."],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        sm = self._build_manager()
        self.assertEqual(sm.get_tool_definitions(), [])
        prompts = sm.get_system_prompts("Need API retry and authorization header guidance")
        self.assertIn("HTTP API interface notes", prompts)
        self.assertEqual(sm.get_tools_for_skill("http-guide"), ["bash"])

    def test_legacy_impl_functions_are_registered_as_tools(self):
        skill_dir = os.path.join(self.skills_dir, "echo-tools")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: echo-tools\ndescription: Echo helper skill\nkind: knowledge\nallowed-tools: [echo]\n---\n"
                "# Skill Purpose\nUse this skill when you need the echo tool.\n"
            )
        with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 2,
                    "name": "echo-tools",
                    "kind": "knowledge",
                    "description": "Echo helper skill",
                    "tool_refs": ["echo"],
                    "workflow": ["Call the echo tool directly for simple echo tasks."],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        with open(os.path.join(skill_dir, "impl.py"), "w", encoding="utf-8") as f:
            f.write("def echo(message):\n    return f'ECHO:{message}'\n")
        sm = self._build_manager()
        tool_names = [item["function"]["name"] for item in sm.get_tool_definitions()]
        self.assertIn("echo", tool_names)
        self.assertEqual(sm.call_tool("echo", {"message": "hello"}, context={}), "ECHO:hello")
        self.assertEqual(sm.get_skill_of_tool("echo"), "echo-tools")

    def test_record_experience_creates_structured_entry_and_summary(self):
        skill_dir = os.path.join(self.skills_dir, "ops-guide")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: ops-guide\ndescription: Runtime operations guide\nkind: knowledge\nexperience: []\n---\n"
                "# Skill Purpose\nUse this guide for runtime operations.\n"
            )
        with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 2,
                    "name": "ops-guide",
                    "kind": "knowledge",
                    "description": "Runtime operations guide",
                    "tool_refs": ["bash"],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        sm = self._build_manager()
        success, message = sm.record_experience(
            experience_text="Always capture stderr before retrying.",
            skill_name="ops-guide",
            tool_name="bash",
            task_type="runtime-debugging",
        )
        self.assertTrue(success, message)
        entries = sm.get_experience_entries("ops-guide")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["tool_name"], "bash")
        with open(os.path.join(skill_dir, "SKILL.md"), "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Always capture stderr before retrying.", content)

    def test_record_experience_without_skill_uses_general_experience(self):
        sm = self._build_manager()
        success, message = sm.record_experience("Retry once after transient network failures.")
        self.assertTrue(success, message)
        self.assertIn("general-experience", sm.skill_records)
        entries = sm.get_experience_entries("general-experience")
        self.assertEqual(len(entries), 1)
        self.assertIn("transient network failures", entries[0]["experience_text"])

    def test_general_experience_registers_dedicated_tool(self):
        general_dir = os.path.join(self.skills_dir, "general-experience")
        os.makedirs(general_dir, exist_ok=True)
        with open(os.path.join(general_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: general-experience\ndescription: General experience\nkind: knowledge\nallowed-tools: [record_general_experience]\nexperience: []\n---\n"
                "# Skill Purpose\nCapture cross-task lessons.\n"
            )
        with open(os.path.join(general_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 2,
                    "name": "general-experience",
                    "kind": "knowledge",
                    "description": "General experience",
                    "tool_refs": ["record_general_experience"],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        with open(os.path.join(general_dir, "impl.py"), "w", encoding="utf-8") as f:
            f.write(
                "def record_general_experience(experience, _context=None):\n"
                "    sm = (_context or {}).get('skill_manager')\n"
                "    ok, _ = sm.record_experience(experience_text=experience, skill_name='general-experience')\n"
                "    return 'ok' if ok else 'error'\n"
            )
        sm = self._build_manager()
        tool_names = [item["function"]["name"] for item in sm.get_tool_definitions()]
        self.assertIn("record_general_experience", tool_names)


if __name__ == "__main__":
    unittest.main()
