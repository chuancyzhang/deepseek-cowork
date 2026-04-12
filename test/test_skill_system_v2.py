import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

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

    def _copy_repo_skill(self, skill_name):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        source_dir = os.path.join(repo_root, "skills", skill_name)
        target_dir = os.path.join(self.skills_dir, skill_name)
        shutil.copytree(source_dir, target_dir)
        return target_dir

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

    def test_explicit_tool_exports_are_registered_before_legacy_reflection(self):
        skill_dir = os.path.join(self.skills_dir, "interaction-tools")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: interaction-tools\ndescription: Explicit tool exports\nkind: knowledge\nallowed-tools: [structured_echo, legacy_echo]\n---\n"
                "# Skill Purpose\nUse this skill for explicit tool export coverage.\n"
            )
        with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 2,
                    "name": "interaction-tools",
                    "kind": "knowledge",
                    "description": "Explicit tool exports",
                    "tool_refs": ["structured_echo", "legacy_echo"],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        with open(os.path.join(skill_dir, "impl.py"), "w", encoding="utf-8") as f:
            f.write(
                "def _structured_echo_impl(message, _context=None):\n"
                "    return {'echo': message, 'session_id': (_context or {}).get('session_id', '')}\n\n"
                "def legacy_echo(message):\n"
                "    return f'LEGACY:{message}'\n\n"
                "TOOL_EXPORTS = [\n"
                "    {\n"
                "        'name': 'structured_echo',\n"
                "        'handler': _structured_echo_impl,\n"
                "        'description': 'Structured echo',\n"
                "        'parameters': {\n"
                "            'type': 'object',\n"
                "            'properties': {\n"
                "                'message': {'type': 'string'},\n"
                "                'options': {\n"
                "                    'type': 'array',\n"
                "                    'items': {\n"
                "                        'type': 'object',\n"
                "                        'properties': {'label': {'type': 'string'}},\n"
                "                    },\n"
                "                },\n"
                "            },\n"
                "            'required': ['message'],\n"
                "        },\n"
                "        'requires_user_interaction': True,\n"
                "        'result_format': 'structured_json',\n"
                "    }\n"
                "]\n"
            )

        sm = self._build_manager()

        tool_names = [item["function"]["name"] for item in sm.get_tool_definitions()]
        self.assertIn("structured_echo", tool_names)
        self.assertIn("legacy_echo", tool_names)

        structured_record = sm.tool_records["structured_echo"]
        self.assertTrue(structured_record["requires_user_interaction"])
        self.assertEqual(structured_record["result_format"], "structured_json")
        self.assertIn("options", structured_record["parameters_schema"]["properties"])

        structured_result = sm.call_tool(
            "structured_echo",
            {"message": "hello"},
            context={"session_id": "session-9"},
        )
        self.assertEqual(structured_result["echo"], "hello")
        self.assertEqual(structured_result["session_id"], "session-9")
        self.assertEqual(sm.call_tool("legacy_echo", {"message": "hi"}, context={}), "LEGACY:hi")

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

    def test_import_skill_without_impl_accepts_legacy_string_metadata(self):
        source_root = tempfile.mkdtemp(dir=self.temp_dir)
        try:
            source_dir = os.path.join(source_root, "portable-guide")
            os.makedirs(source_dir, exist_ok=True)
            with open(os.path.join(source_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(
                    "---\n"
                    "name: portable-guide\n"
                    "description: Portable shell notes\n"
                    "kind: knowledge\n"
                    "allowed-tools: bash\n"
                    "---\n"
                    "# Skill Purpose\nUse this skill for shell portability notes.\n"
                )
            with open(os.path.join(source_dir, "skill.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 2,
                        "name": "portable-guide",
                        "kind": "knowledge",
                        "description": "Portable shell notes",
                        "tool_refs": "bash",
                        "tags": "shell, portability",
                        "triggers": "shell compatibility",
                        "references": "notes.md",
                        "experience_policy": "experience/entries.jsonl",
                        "disclosure_level_defaults": "brief",
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            with open(os.path.join(source_dir, "notes.md"), "w", encoding="utf-8") as f:
                f.write("Use POSIX-safe syntax when scripts run across environments.\n")

            sm = self._build_manager()
            success, message = sm.import_skill(source_dir)
            self.assertTrue(success, message)

            sm.load_skills()
            self.assertIn("portable-guide", sm.skill_records)
            record = sm.skill_records["portable-guide"]
            self.assertEqual(record["tool_refs"], ["bash"])
            self.assertEqual(record["spec"]["tags"], ["shell", "portability"])
            self.assertEqual(record["spec"]["triggers"], ["shell compatibility"])
            self.assertEqual(record["spec"]["references"], ["notes.md"])
            self.assertEqual(record["spec"]["experience_policy"]["entry_storage"], "experience/entries.jsonl")
            self.assertEqual(record["spec"]["disclosure_level_defaults"]["default_prompt_level"], "brief")
        finally:
            shutil.rmtree(source_root, ignore_errors=True)

    def test_agent_skill_directory_exposes_script_entries_without_registering_script_tools(self):
        skill_dir = os.path.join(self.skills_dir, "native-agent-skill")
        os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: native-agent-skill\ndescription: Native skill package\nkind: knowledge\n---\n"
                "# Skill Purpose\nUse this imported skill.\n"
            )
        with open(os.path.join(skill_dir, "scripts", "hello.py"), "w", encoding="utf-8") as f:
            f.write("print('hello')\n")

        sm = self._build_manager()
        record = sm.skill_records["native-agent-skill"]
        self.assertEqual(record["spec"]["script_refs"], [os.path.normpath("scripts\\hello.py")])
        self.assertEqual(record["spec"]["script_entries"][0]["runtime"], "python")
        self.assertEqual(record["spec"]["script_entries"][0]["name"], "hello")
        tool_names = [item["function"]["name"] for item in sm.get_tool_definitions()]
        self.assertNotIn("hello", tool_names)
        full_prompt = sm.get_full_skill_prompt("native-agent-skill") or ""
        self.assertIn("## Skill Scripts", full_prompt)
        self.assertIn("scripts\\hello.py", full_prompt)

    def test_skill_dependencies_are_preserved_and_prepared(self):
        skill_dir = os.path.join(self.skills_dir, "dependency-skill")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: dependency-skill\ndescription: Dependency skill\nkind: knowledge\n---\n"
                "# Skill Purpose\nUse this skill to test dependency metadata.\n"
            )
        with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 2,
                    "name": "dependency-skill",
                    "kind": "knowledge",
                    "description": "Dependency skill",
                    "python_dependencies": ["requests"],
                    "node_dependencies": ["lodash"],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        with patch("core.skill_manager.install_skill_dependencies", return_value={"ok": True, "message": "ready"}) as installer:
            sm = self._build_manager()

        installer.assert_called_with(
            "dependency-skill",
            python_dependencies=["requests"],
            node_dependencies=["lodash"],
        )
        record = sm.skill_records["dependency-skill"]
        self.assertEqual(record["spec"]["python_dependencies"], ["requests"])
        self.assertEqual(record["spec"]["node_dependencies"], ["lodash"])

    def test_command_tools_skill_is_discoverable_and_registers_expected_tools(self):
        self._copy_repo_skill("command-tools")

        sm = self._build_manager()

        self.assertIn("command-tools", sm.skill_records)
        record = sm.skill_records["command-tools"]
        self.assertEqual(record["tool_refs"], ["bash", "glob", "grep", "run_skill_script"])
        tool_names = [item["function"]["name"] for item in sm.get_tool_definitions()]
        self.assertIn("bash", tool_names)
        self.assertIn("glob", tool_names)
        self.assertIn("grep", tool_names)
        self.assertIn("run_skill_script", tool_names)

    def test_system_tools_skill_only_exposes_environment_automation_tools(self):
        self._copy_repo_skill("command-tools")
        self._copy_repo_skill("system-tools")

        sm = self._build_manager()

        self.assertIn("system-tools", sm.skill_records)
        self.assertCountEqual(
            sm.get_tools_for_skill("system-tools"),
            ["system_automate", "build_app_index", "find_app", "launch_app", "open_with"],
        )
        record = sm.skill_records["system-tools"]
        self.assertEqual(record["tool_refs"], ["system_automate", "build_app_index", "find_app", "launch_app", "open_with"])

    def test_run_skill_script_uses_skill_dependency_flow_and_sandbox_runner(self):
        skill_dir = os.path.join(self.skills_dir, "scripted-skill")
        os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: scripted-skill\ndescription: Scripted skill\nkind: knowledge\n---\n"
                "# Skill Purpose\nExecute scripts.\n"
            )
        with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 2,
                    "name": "scripted-skill",
                    "kind": "knowledge",
                    "description": "Scripted skill",
                    "python_dependencies": ["requests"],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        with open(os.path.join(skill_dir, "scripts", "hello.py"), "w", encoding="utf-8") as f:
            f.write("print('hello')\n")

        sm = self._build_manager()
        sm.skill_records["scripted-skill"]["dependency_status"] = {"ok": False, "message": "missing"}

        module_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "command-tools", "impl.py")
        spec = importlib.util.spec_from_file_location("command_tools_impl_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with patch.object(sm, "_prepare_skill_dependencies", return_value={"ok": True, "message": "ready"}) as prepare_mock:
            with patch.object(
                module,
                "run_skill_script_in_sandbox",
                return_value={
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "hello\n",
                    "stderr": "",
                    "runtime": "python",
                    "command": "python hello.py",
                    "cwd": skill_dir,
                },
            ) as run_mock:
                payload = module.run_skill_script(
                    "scripted-skill",
                    "hello",
                    args=["--flag"],
                    _context={"skill_manager": sm},
                )

        result = json.loads(payload)
        self.assertTrue(result["ok"])
        self.assertEqual(result["runtime"], "python")
        self.assertEqual(result["script_path"], os.path.normpath("scripts\\hello.py"))
        prepare_mock.assert_called_once_with("scripted-skill", skill_dir)
        run_mock.assert_called_once()
        called_args, called_kwargs = run_mock.call_args
        self.assertEqual(called_args[0], "scripted-skill")
        self.assertTrue(called_args[1].endswith(os.path.normpath("scripts\\hello.py")))
        self.assertEqual(called_args[2], "python")
        self.assertEqual(called_kwargs["args"], ["--flag"])
        self.assertEqual(called_kwargs["cwd"], skill_dir)

    def test_import_skill_adapts_openclaw_folder_into_experience_package(self):
        source_root = tempfile.mkdtemp(dir=self.temp_dir)
        try:
            source_dir = os.path.join(source_root, "openclaw-guide")
            os.makedirs(os.path.join(source_dir, "prompts"), exist_ok=True)
            with open(os.path.join(source_dir, "openclaw.json"), "w", encoding="utf-8") as f:
                json.dump({"name": "openclaw-guide"}, f)
            with open(os.path.join(source_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write("# External Skill\n\nOriginal OpenClaw instructions.\n")

            sm = self._build_manager()
            success, message = sm.import_skill(source_dir)
            self.assertTrue(success, message)

            sm.load_skills()
            self.assertIn("openclaw-guide", sm.skill_records)
            record = sm.skill_records["openclaw-guide"]
            self.assertEqual(record["spec"]["creation_hints"]["source_format"], "openclaw")
            self.assertEqual(record["tool_refs"], [])
            self.assertIn("openclaw", record["spec"]["tags"])
            self.assertTrue(os.path.exists(os.path.join(self.ai_skills_dir, "openclaw-guide", "references", "source-SKILL.md")))
        finally:
            shutil.rmtree(source_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
