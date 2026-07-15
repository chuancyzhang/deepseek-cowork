import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
import zipfile
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

    def _build_manager_with_enabled(self, enabled):
        class ConfigStub:
            def __init__(self, names):
                self.names = set(names)

            def is_skill_enabled(self, skill_name, default_enabled=True):
                if skill_name in self.names:
                    return True
                return default_enabled

            def get_mcp_servers(self):
                return []

            def get(self, _key, default=None):
                return default

        sm = SkillManager(workspace_dir=self.temp_dir, config_manager=ConfigStub(enabled))
        sm.skills_dirs = [self.skills_dir, self.ai_skills_dir]
        sm.load_skills()
        return sm

    def _build_light_manager(self):
        sm = SkillManager.__new__(SkillManager)
        sm.workspace_dir = self.temp_dir
        sm.config_manager = None
        sm.skills_dirs = [self.skills_dir, self.ai_skills_dir]
        sm.load_skills = lambda: None
        return sm

    def _copy_repo_skill(self, skill_name):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        source_dir = os.path.join(repo_root, "skills", skill_name)
        target_dir = os.path.join(self.skills_dir, skill_name)
        shutil.copytree(source_dir, target_dir)
        return target_dir

    def _copy_repo_ai_skill(self, skill_name):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        source_dir = os.path.join(repo_root, "ai_skills", skill_name)
        target_dir = os.path.join(self.ai_skills_dir, skill_name)
        shutil.copytree(source_dir, target_dir)
        return target_dir

    def test_visualize_plugin_is_disabled_by_default_and_registers_no_tools(self):
        self._copy_repo_ai_skill("visualize")
        manager = self._build_manager()

        self.assertNotIn("visualize", manager.skill_records)
        tool_names = {
            (item.get("function") or {}).get("name")
            for item in manager.get_tool_definitions()
        }
        self.assertNotIn("run_visualization_python", tool_names)
        self.assertNotIn("finalize_inline_visualization", tool_names)

    def test_visualize_plugin_registers_tools_only_when_enabled(self):
        self._copy_repo_ai_skill("visualize")
        manager = self._build_manager_with_enabled({"visualize"})

        self.assertIn("visualize", manager.skill_records)
        self.assertEqual(
            set(manager.get_tools_for_skill("visualize")),
            {"run_visualization_python", "finalize_inline_visualization"},
        )

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

    def test_skill_config_fields_are_normalized_and_report_status(self):
        skill_dir = os.path.join(self.skills_dir, "doc-skill")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: doc-skill\ndescription: Docs\nkind: knowledge\n---\n# Docs\n")
        with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 2,
                    "name": "doc-skill",
                    "kind": "knowledge",
                    "description": "Docs",
                    "config_fields": [
                        {"name": "app_id", "label": "App ID", "required": True, "env": "DOC_APP_ID"},
                        {"name": "secret", "kind": "secret", "required": True, "env": "DOC_SECRET"},
                    ],
                },
                f,
            )

        class ConfigStub:
            def is_skill_enabled(self, _skill_name, default_enabled=True):
                return default_enabled

            def get_mcp_servers(self):
                return []

            def get_skill_config(self, skill_name):
                return {"app_id": "cli_x"} if skill_name == "doc-skill" else {}

        sm = SkillManager(workspace_dir=self.temp_dir, config_manager=ConfigStub())
        sm.skills_dirs = [self.skills_dir, self.ai_skills_dir]
        sm.load_skills()

        fields = sm.get_skill_config_fields("doc-skill")
        self.assertEqual(fields[0]["env"], "DOC_APP_ID")
        status = sm.get_skill_config_status("doc-skill")
        self.assertFalse(status["complete"])
        self.assertEqual(status["missing_required"], ["secret"])

    def test_build_skill_config_env_requires_required_fields(self):
        skill_dir = os.path.join(self.skills_dir, "doc-skill")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: doc-skill\ndescription: Docs\nkind: knowledge\n---\n# Docs\n")
        with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 2,
                    "name": "doc-skill",
                    "kind": "knowledge",
                    "description": "Docs",
                    "config_fields": [
                        {"name": "app_id", "required": True, "env": "DOC_APP_ID"},
                        {"name": "secret", "kind": "secret", "required": True, "env": "DOC_SECRET"},
                    ],
                },
                f,
            )

        class ConfigStub:
            def __init__(self, values):
                self.values = values

            def is_skill_enabled(self, _skill_name, default_enabled=True):
                return default_enabled

            def get_mcp_servers(self):
                return []

            def get_skill_config(self, _skill_name):
                return self.values

        sm = SkillManager(workspace_dir=self.temp_dir, config_manager=ConfigStub({"app_id": "cli_x"}))
        sm.skills_dirs = [self.skills_dir, self.ai_skills_dir]
        sm.load_skills()

        with self.assertRaises(ValueError):
            sm.build_skill_config_env("doc-skill")

        sm.config_manager = ConfigStub({"app_id": "cli_x", "secret": "shh"})
        self.assertEqual(
            sm.build_skill_config_env("doc-skill"),
            {"DOC_APP_ID": "cli_x", "DOC_SECRET": "shh"},
        )

    def test_bundled_document_skills_have_runtime_script_entries(self):
        for skill_name in ("tencent-docs", "feishu-docs", "dingtalk-docs"):
            self._copy_repo_ai_skill(skill_name)

        sm = self._build_manager()
        skills = {item["name"]: item for item in sm.get_all_skills()}

        for skill_name in ("tencent-docs", "feishu-docs", "dingtalk-docs"):
            with self.subTest(skill_name=skill_name):
                self.assertIn(skill_name, skills)
                self.assertFalse(skills[skill_name]["enabled"])
                self.assertTrue(skills[skill_name]["config_fields"])
                self.assertTrue(skills[skill_name]["script_entries"])
                self.assertEqual(skills[skill_name]["source_type"], "bundled_plugin")
                self.assertEqual(skills[skill_name]["source_format"], "agent_skill")

    def test_bundled_knowledge_mcp_skills_are_default_off(self):
        for skill_name in ("weknora", "showdoc-mcp", "airflow", "superset-mcp"):
            self._copy_repo_ai_skill(skill_name)

        sm = self._build_manager()
        skills = {item["name"]: item for item in sm.get_all_skills()}

        for skill_name in ("weknora", "showdoc-mcp", "airflow", "superset-mcp"):
            with self.subTest(skill_name=skill_name):
                self.assertIn(skill_name, skills)
                self.assertFalse(skills[skill_name]["enabled"])
                self.assertEqual(skills[skill_name]["source_type"], "bundled_plugin")
                self.assertEqual(skills[skill_name]["source_format"], "agent_skill")
                self.assertTrue(skills[skill_name]["config_fields"])

        superset_presets = sm.get_skill_mcp_server_presets("superset-mcp")
        self.assertEqual(superset_presets[0]["transport"], "streamable_http")
        self.assertEqual(superset_presets[0]["url"], "{{SUPERSET_MCP_URL}}")
        self.assertEqual(superset_presets[0]["auth"]["type"], "superset_password")
        self.assertEqual(superset_presets[0].get("command"), "")

        weknora_presets = sm.get_skill_mcp_server_presets("weknora")
        self.assertEqual(weknora_presets[0]["runtime"], "skill_python")
        self.assertEqual(weknora_presets[0]["entrypoint"], "scripts/run_mcp.py")

        airflow_info = skills["airflow"]
        self.assertTrue(any(entry.get("name") == "run_af" for entry in airflow_info["script_entries"]))
        self.assertTrue(any("af" in item.lower() for item in airflow_info["tags"]))
        self.assertIn("AF_READ_ONLY", json.dumps(airflow_info, ensure_ascii=False))

    def test_skill_mcp_presets_materialize_from_config(self):
        for skill_name in ("showdoc-mcp", "airflow", "superset-mcp", "weknora"):
            self._copy_repo_ai_skill(skill_name)

        class ConfigStub:
            def __init__(self, values):
                self.values = values

            def is_skill_enabled(self, _skill_name, default_enabled=True):
                return default_enabled

            def get_mcp_servers(self):
                return []

            def get(self, _key, default=None):
                return default

            def get_skill_config(self, skill_name):
                return self.values.get(skill_name, {})

        sm = SkillManager(
            workspace_dir=self.temp_dir,
            config_manager=ConfigStub(
                {
                    "showdoc-mcp": {
                        "SHOWDOC_HOST": "https://showdoc.example",
                        "SHOWDOC_LOGIN_SECRET_KEY": "secret",
                        "SHOWDOC_PROJECT_NAME": "Backend",
                    },
                    "airflow": {
                        "AIRFLOW_API_URL": "https://airflow.example",
                        "AIRFLOW_AUTH_TOKEN": "token",
                        "AF_READ_ONLY": "true",
                    },
                    "superset-mcp": {
                        "SUPERSET_BASE_URL": "https://superset.example",
                        "SUPERSET_MCP_URL": "http://localhost:5008/mcp",
                        "SUPERSET_USERNAME": "admin",
                        "SUPERSET_PASSWORD": "secret",
                        "SUPERSET_PROVIDER": "db",
                        "SUPERSET_MCP_TIMEOUT_SECONDS": "45",
                    },
                    "weknora": {
                        "WEKNORA_BASE_URL": "https://weknora.example/api/v1",
                        "WEKNORA_API_KEY": "sk-test",
                    },
                }
            ),
        )
        sm.skills_dirs = [self.skills_dir, self.ai_skills_dir]
        sm.load_skills()

        showdoc = sm.build_skill_mcp_server_configs("showdoc-mcp")
        self.assertTrue(showdoc["ok"], showdoc.get("error"))
        self.assertEqual(showdoc["servers"][0]["id"], "showdoc")
        self.assertEqual(showdoc["servers"][0]["command"], "npx")
        self.assertIn("Backend", showdoc["servers"][0]["args"])

        with patch.object(sm, "_prepare_skill_dependencies", return_value={"ok": True, "message": "ready"}), \
             patch("core.skill_manager.get_runtime_executable", return_value=r"C:\Cowork\python.exe"):
            airflow = sm.build_skill_mcp_server_configs("airflow")
            weknora = sm.build_skill_mcp_server_configs("weknora")
        self.assertTrue(airflow["ok"], airflow.get("error"))
        self.assertEqual(airflow["servers"][0]["command"], r"C:\Cowork\python.exe")
        self.assertEqual(airflow["servers"][0]["runtime_skill"], "airflow")
        self.assertEqual(airflow["servers"][0]["args"][-2:], ["--transport", "stdio"])
        self.assertEqual(airflow["servers"][0]["env"]["AF_READ_ONLY"], "true")

        self.assertTrue(weknora["ok"], weknora.get("error"))
        self.assertEqual(weknora["servers"][0]["runtime_skill"], "weknora")
        self.assertEqual(weknora["servers"][0]["env"]["WEKNORA_API_KEY"], "sk-test")

        superset = sm.build_skill_mcp_server_configs("superset-mcp")
        self.assertTrue(superset["ok"], superset.get("error"))
        self.assertEqual(superset["servers"][0]["transport"], "streamable_http")
        self.assertEqual(superset["servers"][0]["url"], "http://localhost:5008/mcp")
        self.assertEqual(superset["servers"][0]["headers"], {})
        self.assertEqual(superset["servers"][0]["auth"]["type"], "superset_password")
        self.assertEqual(superset["servers"][0]["timeout_seconds"], 45)

    def test_skill_mcp_presets_fail_on_missing_required_config(self):
        for skill_name in ("showdoc-mcp", "airflow", "superset-mcp"):
            self._copy_repo_ai_skill(skill_name)

        class ConfigStub:
            def is_skill_enabled(self, _skill_name, default_enabled=True):
                return default_enabled

            def get_mcp_servers(self):
                return []

            def get(self, _key, default=None):
                return default

            def get_skill_config(self, skill_name):
                if skill_name == "airflow":
                    return {"AIRFLOW_API_URL": "https://airflow.example"}
                if skill_name == "showdoc-mcp":
                    return {"SHOWDOC_HOST": "https://showdoc.example"}
                if skill_name == "superset-mcp":
                    return {"SUPERSET_MCP_URL": "http://localhost:5008/mcp"}
                return {}

        sm = SkillManager(workspace_dir=self.temp_dir, config_manager=ConfigStub())
        sm.skills_dirs = [self.skills_dir, self.ai_skills_dir]
        sm.load_skills()

        self.assertFalse(sm.build_skill_mcp_server_configs("showdoc-mcp")["ok"])
        airflow = sm.build_skill_mcp_server_configs("airflow")
        self.assertFalse(airflow["ok"])
        self.assertIn("AIRFLOW_AUTH_TOKEN", airflow["error"])
        superset = sm.build_skill_mcp_server_configs("superset-mcp")
        self.assertFalse(superset["ok"])
        self.assertIn("SUPERSET_BASE_URL", superset["error"])

    def test_skill_config_select_default_is_used_for_status_and_env(self):
        skill_dir = os.path.join(self.skills_dir, "select-skill")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: select-skill\ndescription: Select\nkind: knowledge\n---\n# Select\n")
        with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "name": "select-skill",
                    "default_enabled": True,
                    "config_fields": [
                        {
                            "name": "PROVIDER",
                            "kind": "select",
                            "required": True,
                            "default": "db",
                            "options": [
                                {"value": "db", "label": "Database"},
                                {"value": "ldap", "label": "LDAP"},
                            ],
                        }
                    ],
                },
                f,
            )
        sm = self._build_manager()
        fields = sm.get_skill_config_fields("select-skill")
        self.assertEqual(fields[0]["kind"], "select")
        self.assertEqual(fields[0]["options"][1]["value"], "ldap")
        self.assertTrue(sm.get_skill_config_status("select-skill")["complete"])
        self.assertEqual(sm.build_skill_config_env("select-skill"), {"PROVIDER": "db"})

    def test_airflow_run_af_uses_python_cli_and_keeps_read_only_guard(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script_path = os.path.join(repo_root, "ai_skills", "airflow", "scripts", "run_af.py")
        calls = []
        cli_module = types.ModuleType("astro_airflow_mcp.cli.main")

        def fake_cli_main():
            calls.append(list(sys.argv))
            raise SystemExit(0)

        cli_module.cli_main = fake_cli_main
        modules = {
            "astro_airflow_mcp": types.ModuleType("astro_airflow_mcp"),
            "astro_airflow_mcp.cli": types.ModuleType("astro_airflow_mcp.cli"),
            "astro_airflow_mcp.cli.main": cli_module,
        }
        spec = importlib.util.spec_from_file_location("test_airflow_run_af", script_path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules), patch.dict(os.environ, {"AF_READ_ONLY": "true"}):
            spec.loader.exec_module(module)
            with patch.object(sys, "argv", ["run_af.py", "health"]):
                self.assertEqual(module.main(), 0)
            with patch.object(sys, "argv", ["run_af.py", "runs", "trigger", "demo"]):
                self.assertEqual(module.main(), 3)
        self.assertEqual(calls, [["af", "health"]])

    def test_frozen_internal_ai_skills_are_discovered_as_default_off_plugins(self):
        exe_dir = os.path.join(self.temp_dir, "dist", "deepseek-cowork")
        internal_skills = os.path.join(exe_dir, "_internal", "skills")
        internal_ai_skills = os.path.join(exe_dir, "_internal", "ai_skills")
        os.makedirs(os.path.join(internal_skills, "builtin-guide"), exist_ok=True)
        os.makedirs(os.path.join(internal_ai_skills, "document-reader"), exist_ok=True)
        with open(os.path.join(internal_skills, "builtin-guide", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: builtin-guide\ndescription: Builtin guide\nkind: knowledge\n---\n"
                "# Builtin Guide\n"
            )
        with open(os.path.join(internal_ai_skills, "document-reader", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\n"
                "name: document-reader\n"
                "description: Document reader\n"
                "kind: knowledge\n"
                "source_type: bundled_plugin\n"
                "default_enabled: false\n"
                "allowed-tools: [document_read]\n"
                "---\n"
                "# Document Reader\n"
            )
        with open(os.path.join(internal_ai_skills, "document-reader", "skill.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "name": "document-reader",
                    "kind": "knowledge",
                    "description": "Document reader",
                    "source_type": "bundled_plugin",
                    "default_enabled": False,
                    "tool_refs": ["document_read"],
                },
                f,
            )

        class ConfigStub:
            def is_skill_enabled(self, _skill_name, default_enabled=True):
                return default_enabled

            def get_mcp_servers(self):
                return []

            def get(self, _key, default=None):
                return default

        executable = os.path.join(exe_dir, "deepseek-cowork.exe")
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", executable):
            sm = SkillManager(workspace_dir=self.temp_dir, config_manager=ConfigStub())

        self.assertIn(internal_skills, sm.skills_dirs)
        self.assertIn(internal_ai_skills, sm.skills_dirs)
        self.assertIn("builtin-guide", sm.skill_records)
        self.assertNotIn("document-reader", sm.skill_records)
        self.assertNotIn("document_read", sm.tools)

        skills = {item["name"]: item for item in sm.get_all_skills()}
        self.assertIn("document-reader", skills)
        self.assertEqual(skills["document-reader"].get("source_type"), "bundled_plugin")
        self.assertFalse(skills["document-reader"].get("enabled"))

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

    def test_mcp_tools_are_discovered_on_demand_when_loading_is_deferred(self):
        class ConfigStub:
            def is_skill_enabled(self, _skill_name, default_enabled=True):
                return default_enabled

            def get_mcp_servers(self):
                return [
                    {
                        "id": "demo",
                        "name": "Demo MCP",
                        "enabled": True,
                        "transport": "stdio",
                        "command": "demo",
                        "args": [],
                    }
                ]

            def get(self, _key, default=None):
                return default

        tool_payload = {
            "ok": True,
            "tools": [
                {
                    "name": "echo",
                    "description": "Echo from Demo MCP",
                    "input_schema": {"type": "object", "properties": {}, "required": []},
                }
            ],
        }
        with patch("core.skill_manager.mcp_package_available", return_value=True), patch(
            "core.skill_manager.list_mcp_server_tools", return_value=tool_payload
        ) as list_tools:
            sm = SkillManager(
                workspace_dir=self.temp_dir,
                config_manager=ConfigStub(),
                auto_load=False,
                load_mcp_tools=False,
            )
            sm.skills_dirs = [self.skills_dir, self.ai_skills_dir]
            sm.load_skills(load_mcp_tools=False)

            list_tools.assert_not_called()
            result = sm.call_tool(
                "tool_search",
                {"query": "Demo MCP echo"},
                context={"discovered_tool_names": set(), "run_context": {"mode": "execution"}},
            )

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["discovered_tools"])
        self.assertEqual(list_tools.call_count, 1)

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

    def test_export_skill_creates_zip_and_skips_cache_directories(self):
        skill_dir = os.path.join(self.skills_dir, "portable-guide")
        os.makedirs(os.path.join(skill_dir, "__pycache__"), exist_ok=True)
        os.makedirs(os.path.join(skill_dir, "build"), exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: portable-guide\ndescription: Portable shell notes\nkind: knowledge\n---\n"
                "# Skill Purpose\nUse this skill for shell portability notes.\n"
            )
        with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 2,
                    "name": "portable-guide",
                    "kind": "knowledge",
                    "description": "Portable shell notes",
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        with open(os.path.join(skill_dir, "notes.md"), "w", encoding="utf-8") as f:
            f.write("notes\n")
        with open(os.path.join(skill_dir, "__pycache__", "cached.pyc"), "wb") as f:
            f.write(b"compiled")
        with open(os.path.join(skill_dir, "build", "artifact.txt"), "w", encoding="utf-8") as f:
            f.write("artifact\n")

        sm = self._build_manager()
        zip_path = os.path.join(self.temp_dir, "portable-guide.zip")
        success, message = sm.export_skill("portable-guide", zip_path)

        self.assertTrue(success, message)
        self.assertTrue(os.path.isfile(zip_path))
        with zipfile.ZipFile(zip_path, "r") as archive:
            names = set(archive.namelist())
        self.assertIn("portable-guide/SKILL.md", names)
        self.assertIn("portable-guide/skill.json", names)
        self.assertIn("portable-guide/notes.md", names)
        self.assertNotIn("portable-guide/__pycache__/cached.pyc", names)
        self.assertNotIn("portable-guide/build/artifact.txt", names)

    def test_export_skill_collection_creates_importable_multi_skill_zip(self):
        for skill_name in ("portable-guide", "claim-helper"):
            skill_dir = os.path.join(self.skills_dir, skill_name)
            os.makedirs(skill_dir, exist_ok=True)
            with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(
                    f"---\nname: {skill_name}\ndescription: {skill_name}\nkind: knowledge\n---\n"
                    "# Skill Purpose\nUse this skill.\n"
                )
            with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 2,
                        "name": skill_name,
                        "kind": "knowledge",
                        "description": skill_name,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

        sm = self._build_light_manager()
        zip_path = os.path.join(self.temp_dir, "skill-collection.zip")
        success, message = sm.export_skill_collection(["portable-guide", "claim-helper"], zip_path)

        self.assertTrue(success, message)
        with zipfile.ZipFile(zip_path, "r") as archive:
            names = set(archive.namelist())
        self.assertIn("portable-guide/SKILL.md", names)
        self.assertIn("claim-helper/SKILL.md", names)

    def test_skill_center_file_editing_allows_only_ai_skills(self):
        builtin_dir = os.path.join(self.skills_dir, "builtin-guide")
        custom_dir = os.path.join(self.ai_skills_dir, "custom-guide")
        for skill_dir, skill_name in ((builtin_dir, "builtin-guide"), (custom_dir, "custom-guide")):
            os.makedirs(skill_dir, exist_ok=True)
            with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(
                    f"---\nname: {skill_name}\ndescription: {skill_name}\nkind: knowledge\n---\n"
                    "# Skill Purpose\nUse this skill.\n"
                )

        sm = self._build_light_manager()

        self.assertFalse(sm.is_skill_editable("builtin-guide"))
        self.assertTrue(sm.is_skill_editable("custom-guide"))
        rejected = sm.write_skill_file("builtin-guide", "SKILL.md", "nope")
        self.assertFalse(rejected["ok"])
        escaped = sm.write_skill_file("custom-guide", "../outside.md", "nope")
        self.assertFalse(escaped["ok"])
        saved = sm.write_skill_file("custom-guide", "notes.md", "hello")
        self.assertTrue(saved["ok"], saved)
        read_back = sm.read_skill_file("custom-guide", "notes.md")
        self.assertEqual(read_back["content"], "hello")

    def test_delete_skill_collection_only_deletes_user_skills(self):
        builtin_dir = os.path.join(self.skills_dir, "builtin-guide")
        custom_dir = os.path.join(self.ai_skills_dir, "custom-guide")
        for skill_dir, skill_name in ((builtin_dir, "builtin-guide"), (custom_dir, "custom-guide")):
            os.makedirs(skill_dir, exist_ok=True)
            with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(
                    f"---\nname: {skill_name}\ndescription: {skill_name}\nkind: knowledge\n---\n"
                    "# Skill Purpose\nUse this skill.\n"
                )

        sm = self._build_light_manager()
        result = sm.delete_skill_collection(["builtin-guide", "custom-guide", "mcp-server-demo"])

        self.assertTrue(result["ok"], result)
        self.assertFalse(os.path.exists(custom_dir))
        self.assertTrue(os.path.exists(builtin_dir))
        self.assertEqual(len(result["summary"]["deleted"]), 1)
        self.assertEqual(len(result["summary"]["skipped"]), 2)

    def test_validate_skill_reports_invalid_json_and_missing_script(self):
        skill_dir = os.path.join(self.ai_skills_dir, "broken-guide")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: broken-guide\nkind: knowledge\n---\n# Skill Purpose\nBroken.\n")
        with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as f:
            f.write("{bad json")

        sm = self._build_light_manager()
        result = sm.validate_skill("broken-guide")
        self.assertFalse(result["ok"])
        self.assertTrue(any("skill.json is invalid" in item for item in result["issues"]))

    def test_exported_zip_can_be_imported_back_with_original_skill_name(self):
        source_root = tempfile.mkdtemp(dir=self.temp_dir)
        target_root = tempfile.mkdtemp(dir=self.temp_dir)
        try:
            source_skills_dir = os.path.join(source_root, "skills")
            source_ai_skills_dir = os.path.join(source_root, "ai_skills")
            os.makedirs(source_skills_dir, exist_ok=True)
            os.makedirs(source_ai_skills_dir, exist_ok=True)
            source_skill_dir = os.path.join(source_skills_dir, "portable-guide")
            os.makedirs(source_skill_dir, exist_ok=True)
            with open(os.path.join(source_skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(
                    "---\nname: portable-guide\ndescription: Portable shell notes\nkind: knowledge\n---\n"
                    "# Skill Purpose\nUse this skill for shell portability notes.\n"
                )
            with open(os.path.join(source_skill_dir, "skill.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 2,
                        "name": "portable-guide",
                        "kind": "knowledge",
                        "description": "Portable shell notes",
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            with open(os.path.join(source_skill_dir, "notes.md"), "w", encoding="utf-8") as f:
                f.write("notes\n")

            source_manager = SkillManager(workspace_dir=source_root)
            source_manager.skills_dirs = [source_skills_dir, source_ai_skills_dir]
            source_manager.load_skills()

            zip_path = os.path.join(self.temp_dir, "renamed-package.zip")
            success, message = source_manager.export_skill("portable-guide", zip_path)
            self.assertTrue(success, message)

            target_skills_dir = os.path.join(target_root, "skills")
            target_ai_skills_dir = os.path.join(target_root, "ai_skills")
            os.makedirs(target_skills_dir, exist_ok=True)
            os.makedirs(target_ai_skills_dir, exist_ok=True)
            target_manager = SkillManager(workspace_dir=target_root)
            target_manager.skills_dirs = [target_skills_dir, target_ai_skills_dir]
            target_manager.load_skills()

            success, message = target_manager.import_skill(zip_path)
            self.assertTrue(success, message)
            target_manager.load_skills()
            self.assertIn("portable-guide", target_manager.skill_records)
            imported_dir = os.path.join(target_ai_skills_dir, "portable-guide")
            self.assertTrue(os.path.isfile(os.path.join(imported_dir, "SKILL.md")))
            self.assertTrue(os.path.isfile(os.path.join(imported_dir, "skill.json")))
            self.assertTrue(os.path.isfile(os.path.join(imported_dir, "notes.md")))
        finally:
            shutil.rmtree(source_root, ignore_errors=True)
            shutil.rmtree(target_root, ignore_errors=True)

    def test_import_skill_accepts_flat_zip_root(self):
        source_root = tempfile.mkdtemp(dir=self.temp_dir)
        try:
            flat_dir = os.path.join(source_root, "flat-root")
            os.makedirs(flat_dir, exist_ok=True)
            with open(os.path.join(flat_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(
                    "---\nname: flat-zip-skill\ndescription: Flat ZIP skill\nkind: knowledge\n---\n"
                    "# Skill Purpose\nUse this skill from a flat ZIP package.\n"
                )
            with open(os.path.join(flat_dir, "skill.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 2,
                        "name": "flat-zip-skill",
                        "kind": "knowledge",
                        "description": "Flat ZIP skill",
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            with open(os.path.join(flat_dir, "notes.md"), "w", encoding="utf-8") as f:
                f.write("flat zip notes\n")

            zip_path = os.path.join(self.temp_dir, "flat-zip-skill.zip")
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(os.path.join(flat_dir, "SKILL.md"), arcname="SKILL.md")
                archive.write(os.path.join(flat_dir, "skill.json"), arcname="skill.json")
                archive.write(os.path.join(flat_dir, "notes.md"), arcname="notes.md")

            sm = self._build_manager()
            success, message = sm.import_skill(zip_path)
            self.assertTrue(success, message)
            sm.load_skills()
            self.assertIn("flat-zip-skill", sm.skill_records)
        finally:
            shutil.rmtree(source_root, ignore_errors=True)

    def test_import_skill_rejects_existing_name_from_zip(self):
        skill_dir = os.path.join(self.ai_skills_dir, "portable-guide")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: portable-guide\ndescription: Existing skill\nkind: knowledge\n---\n"
                "# Skill Purpose\nExisting skill.\n"
            )

        source_root = tempfile.mkdtemp(dir=self.temp_dir)
        try:
            zip_skill_dir = os.path.join(source_root, "portable-guide")
            os.makedirs(zip_skill_dir, exist_ok=True)
            with open(os.path.join(zip_skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(
                    "---\nname: portable-guide\ndescription: Imported skill\nkind: knowledge\n---\n"
                    "# Skill Purpose\nImported skill.\n"
                )
            with open(os.path.join(zip_skill_dir, "skill.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 2,
                        "name": "portable-guide",
                        "kind": "knowledge",
                        "description": "Imported skill",
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            zip_path = os.path.join(self.temp_dir, "portable-guide-conflict.zip")
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(os.path.join(zip_skill_dir, "SKILL.md"), arcname="portable-guide/SKILL.md")
                archive.write(os.path.join(zip_skill_dir, "skill.json"), arcname="portable-guide/skill.json")

            sm = self._build_manager()
            success, message = sm.import_skill(zip_path)
            self.assertFalse(success)
            self.assertIn("already exists", message)
        finally:
            shutil.rmtree(source_root, ignore_errors=True)

    def test_import_skill_collection_imports_multiple_external_skills(self):
        source_root = tempfile.mkdtemp(dir=self.temp_dir)
        try:
            collection_dir = os.path.join(source_root, "DianJin-SKILLS-main")
            claim_dir = os.path.join(collection_dir, "claim-expert")
            review_dir = os.path.join(collection_dir, "policy-review")
            os.makedirs(claim_dir, exist_ok=True)
            os.makedirs(review_dir, exist_ok=True)
            with open(os.path.join(claim_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(
                    "---\nname: claim-expert\ndescription: Review claim evidence and consistency.\n---\n"
                    "# Claim Expert\n\nReview claim evidence and consistency.\n"
                )
            with open(os.path.join(review_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(
                    "---\nname: policy-review\ndescription: Check policy wording and exceptions.\n---\n"
                    "# Policy Review\n\nCheck policy wording and exceptions.\n"
                )

            sm = self._build_manager()
            success, message = sm.import_skill(collection_dir)
            self.assertTrue(success, message)
            self.assertIn("2 个成功", message)

            self.assertEqual(set(sm.last_imported_skill_names), {"claim-expert", "policy-review"})
            self.assertNotIn("claim-expert", sm.skill_records)
            sm.load_skills()
            self.assertIn("claim-expert", sm.skill_records)
            self.assertIn("policy-review", sm.skill_records)
            self.assertIn("claim-expert", sm.select_relevant_skills("CLAIM EXPERT", limit=5))
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
        self.assertEqual(record["spec"]["execution_surface"], "skill_script")
        self.assertEqual(record["spec"]["prompt_disclosure"], "full_on_match")
        self.assertEqual(record["spec"]["preferred_script_name"], "hello")
        tool_names = [item["function"]["name"] for item in sm.get_tool_definitions()]
        self.assertNotIn("hello", tool_names)
        full_prompt = sm.get_full_skill_prompt("native-agent-skill") or ""
        self.assertIn("## Skill Scripts", full_prompt)
        self.assertIn("scripts\\hello.py", full_prompt)
        self.assertIn("Do not use `glob`, `grep`, or `bash`", full_prompt)
        self.assertIn("script-execution:", record["brief"])

    def test_skill_dependencies_are_preserved_without_installing_during_catalog_load(self):
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

        installer.assert_not_called()
        record = sm.skill_records["dependency-skill"]
        self.assertEqual(record["spec"]["python_dependencies"], ["requests"])
        self.assertEqual(record["spec"]["node_dependencies"], ["lodash"])
        self.assertTrue(record["dependency_status"].get("pending"))

    def test_command_tools_skill_is_discoverable_and_registers_expected_tools(self):
        self._copy_repo_skill("command-tools")

        sm = self._build_manager()

        self.assertIn("command-tools", sm.skill_records)
        record = sm.skill_records["command-tools"]
        self.assertEqual(record["tool_refs"], ["bash", "glob", "grep", "run_node_code", "run_skill_script"])
        tool_names = [item["function"]["name"] for item in sm.get_tool_definitions()]
        self.assertIn("bash", tool_names)
        self.assertIn("glob", tool_names)
        self.assertIn("grep", tool_names)
        self.assertIn("run_node_code", tool_names)
        self.assertIn("run_skill_script", tool_names)

    def test_agent_manager_skill_exports_tool_first_surface(self):
        self._copy_repo_skill("agent-manager")

        sm = self._build_manager()

        self.assertIn("agent-manager", sm.skill_records)
        self.assertCountEqual(
            sm.get_tools_for_skill("agent-manager"),
            ["spawn_agent", "send_input", "wait_agent", "close_agent", "list_agents"],
        )
        tool_names = [item["function"]["name"] for item in sm.get_tool_definitions()]
        self.assertIn("spawn_agent", tool_names)
        self.assertIn("send_input", tool_names)
        self.assertIn("wait_agent", tool_names)
        self.assertIn("close_agent", tool_names)
        self.assertIn("list_agents", tool_names)
        self.assertNotIn("dispatch_agents", tool_names)

    def test_browser_automation_exposes_unified_browser_surface(self):
        self._copy_repo_ai_skill("browser-automation")

        sm = self._build_manager_with_enabled({"browser-automation"})

        self.assertIn("browser-automation", sm.skill_records)
        self.assertCountEqual(
            sm.get_tools_for_skill("browser-automation"),
            ["browser_automate", "get_active_tab_info", "visit_and_screenshot"],
        )
        record = sm.skill_records["browser-automation"]
        self.assertEqual(record["tool_refs"], ["browser_automate", "get_active_tab_info", "visit_and_screenshot"])

    def test_builtin_ppt_agent_ai_skills_load_with_resources(self):
        for skill_name in ("guizang-ppt-skill", "frontend-slides", "huashu-design"):
            self._copy_repo_ai_skill(skill_name)

        sm = self._build_manager()

        for skill_name in ("guizang-ppt-skill", "frontend-slides", "huashu-design"):
            self.assertIn(skill_name, sm.skill_records)
            record = sm.skill_records[skill_name]
            self.assertTrue(record["spec"].get("default_enabled"))
            self.assertEqual(record["spec"].get("source_type"), "builtin_ai_skill")
            self.assertIn("PPT Agent", record["search_text"])
            self.assertTrue(record["spec"].get("references") or record["spec"].get("asset_refs"))
            self.assertTrue(os.path.isfile(os.path.join(record["path"], "SOURCE.md")))

        self.assertIn("huashu-design", sm.select_relevant_skills("PPT Agent Huashu Design 高级感路演", limit=5))
        self.assertIn("frontend-slides", sm.select_relevant_skills("PPT Agent Frontend Slides 技术分享", limit=5))
        self.assertIn("guizang-ppt-skill", sm.select_relevant_skills("PPT Agent 横向翻页 瑞士风", limit=5))

    def test_huashu_builtin_skill_omits_large_media_assets(self):
        skill_dir = self._copy_repo_ai_skill("huashu-design")

        media_files = []
        for root, _dirs, filenames in os.walk(skill_dir):
            for filename in filenames:
                if os.path.splitext(filename)[1].lower() in {".mp3", ".wav", ".mp4"}:
                    media_files.append(os.path.join(root, filename))

        self.assertEqual(media_files, [])

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

    def test_import_skill_installs_standard_agent_skill_with_original_skill_md(self):
        source_root = tempfile.mkdtemp(dir=self.temp_dir)
        try:
            source_dir = os.path.join(source_root, "aihot")
            os.makedirs(source_dir, exist_ok=True)
            original_md = (
                "---\n"
                "name: aihot\n"
                "description: AI HOT 中文 AI 资讯查询 Skill。今天 AI 圈有什么时使用。\n"
                "---\n"
                "# AI HOT Skill\n\n原始规则。\n"
            )
            with open(os.path.join(source_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(original_md)

            sm = self._build_manager()
            success, message = sm.import_skill(source_dir)
            self.assertTrue(success, message)

            self.assertEqual(sm.last_imported_skill_names, ["aihot"])
            self.assertNotIn("aihot", sm.skill_records)
            sm.load_skills()
            self.assertIn("aihot", sm.skill_records)
            record = sm.skill_records["aihot"]
            self.assertEqual(record["spec"]["creation_hints"]["source_format"], "agent_skill")
            self.assertEqual(record["tool_refs"], [])
            self.assertIn("今天 AI 圈有什么", record["spec"]["description"])
            with open(os.path.join(self.ai_skills_dir, "aihot", "SKILL.md"), "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), original_md)
        finally:
            shutil.rmtree(source_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
