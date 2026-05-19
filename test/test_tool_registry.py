import os
import shutil
import sys
import tempfile
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.clarify_mode import RUN_MODE_CLARIFYING, RUN_MODE_EXECUTION
from core.skill_manager import SkillManager
from core.tool_registry import ToolRegistry


class TestToolRegistry(unittest.TestCase):
    def test_mode_filtering_and_deferred_discovery(self):
        registry = ToolRegistry()

        def read_file(path):
            return path

        def write_file(path, content):
            return content

        registry.register(
            "read_file",
            read_file,
            "Read a workspace file",
            {"type": "object", "properties": {}, "required": []},
        )
        registry.register(
            "write_file",
            write_file,
            "Write a workspace file",
            {"type": "object", "properties": {}, "required": []},
        )

        clarifying_names = [item["function"]["name"] for item in registry.definitions(RUN_MODE_CLARIFYING)]
        self.assertIn("read_file", clarifying_names)
        self.assertNotIn("write_file", clarifying_names)

        execution_initial = [
            item["function"]["name"]
            for item in registry.definitions(RUN_MODE_EXECUTION, discovered_tool_names=set())
        ]
        self.assertIn("read_file", execution_initial)
        self.assertNotIn("write_file", execution_initial)

        matches = registry.search("write file", run_mode=RUN_MODE_EXECUTION)
        self.assertEqual(matches[0]["name"], "write_file")
        execution_after_search = [
            item["function"]["name"]
            for item in registry.definitions(
                RUN_MODE_EXECUTION,
                discovered_tool_names={"write_file"},
            )
        ]
        self.assertIn("write_file", execution_after_search)
        self.assertEqual(registry.search("write file", run_mode=RUN_MODE_CLARIFYING), [])

    def test_alias_resolution(self):
        registry = ToolRegistry()

        def read_file(path):
            return path

        registry.register(
            "read_file",
            read_file,
            "Read a workspace file",
            {"type": "object", "properties": {}, "required": []},
            aliases=["open_file"],
        )

        self.assertEqual(registry.resolve_name("open_file"), "read_file")
        self.assertTrue(registry.is_allowed("open_file", RUN_MODE_CLARIFYING))


class TestSkillManagerToolDiscovery(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.temp_dir, "skills")
        os.makedirs(self.skills_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _build_manager(self):
        sm = SkillManager(workspace_dir=self.temp_dir)
        sm.skills_dirs = [self.skills_dir]
        sm.load_skills()
        return sm

    def _copy_repo_skill(self, skill_name):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        source_dir = os.path.join(repo_root, "skills", skill_name)
        target_dir = os.path.join(self.skills_dir, skill_name)
        shutil.copytree(source_dir, target_dir)
        return target_dir

    def test_skill_manager_filters_tools_by_mode_and_discovery(self):
        skill_dir = os.path.join(self.skills_dir, "notes-tools")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "impl.py"), "w", encoding="utf-8") as f:
            f.write(
                "def _read_note(path):\n"
                "    return path\n\n"
                "def _write_note(path, content):\n"
                "    return content\n\n"
                "TOOL_EXPORTS = [\n"
                "    {\n"
                "        'name': 'read_note',\n"
                "        'handler': _read_note,\n"
                "        'description': 'Read note content',\n"
                "        'parameters': {'type': 'object', 'properties': {}, 'required': []},\n"
                "        'read_only': True,\n"
                "        'search_hint': 'notes read text',\n"
                "    },\n"
                "    {\n"
                "        'name': 'write_note',\n"
                "        'handler': _write_note,\n"
                "        'description': 'Write note content',\n"
                "        'parameters': {'type': 'object', 'properties': {}, 'required': []},\n"
                "        'destructive': True,\n"
                "        'search_hint': 'notes write text',\n"
                "    },\n"
                "]\n"
            )

        sm = self._build_manager()

        initial_execution = {
            item["function"]["name"]
            for item in sm.get_tool_definitions(
                run_mode=RUN_MODE_EXECUTION,
                discovered_tool_names=set(),
            )
        }
        self.assertIn("tool_search", initial_execution)
        self.assertNotIn("read_note", initial_execution)
        self.assertNotIn("write_note", initial_execution)

        discovered = set()
        result = sm.call_tool(
            "tool_search",
            {"query": "notes"},
            context={
                "run_context": {"mode": RUN_MODE_EXECUTION},
                "discovered_tool_names": discovered,
            },
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("read_note", discovered)
        self.assertIn("write_note", discovered)

        after_search = {
            item["function"]["name"]
            for item in sm.get_tool_definitions(
                run_mode=RUN_MODE_EXECUTION,
                discovered_tool_names=discovered,
            )
        }
        self.assertIn("read_note", after_search)
        self.assertIn("write_note", after_search)

        clarifying_discovered = {"read_note", "write_note"}
        clarifying_tools = {
            item["function"]["name"]
            for item in sm.get_tool_definitions(
                run_mode=RUN_MODE_CLARIFYING,
                discovered_tool_names=clarifying_discovered,
            )
        }
        self.assertIn("tool_search", clarifying_tools)
        self.assertIn("read_note", clarifying_tools)
        self.assertNotIn("write_note", clarifying_tools)

    def test_publish_artifacts_is_visible_only_in_enterprise_im_context(self):
        self._copy_repo_skill("interaction")
        sm = self._build_manager()

        desktop_tools = {
            item["function"]["name"]
            for item in sm.get_tool_definitions(
                run_mode=RUN_MODE_EXECUTION,
                run_context={"mode": RUN_MODE_EXECUTION},
            )
        }
        self.assertNotIn("publish_artifacts", desktop_tools)

        desktop_search = sm.call_tool(
            "tool_search",
            {"query": "publish artifact file delivery"},
            context={
                "run_context": {"mode": RUN_MODE_EXECUTION},
                "discovered_tool_names": set(),
            },
        )
        self.assertNotIn("publish_artifacts", desktop_search["discovered_tools"])

        feishu_run_context = {
            "mode": RUN_MODE_EXECUTION,
            "im_provider": "feishu",
            "channel": "feishu",
        }
        feishu_tools = {
            item["function"]["name"]
            for item in sm.get_tool_definitions(
                run_mode=RUN_MODE_EXECUTION,
                run_context=feishu_run_context,
            )
        }
        self.assertIn("publish_artifacts", feishu_tools)

        feishu_search = sm.call_tool(
            "tool_search",
            {"query": "publish artifact file delivery"},
            context={
                "run_context": feishu_run_context,
                "discovered_tool_names": set(),
            },
        )
        self.assertIn("publish_artifacts", feishu_search["discovered_tools"])

        for provider in ("dingtalk", "wecom"):
            run_context = {
                "mode": RUN_MODE_EXECUTION,
                "im_provider": provider,
                "channel": provider,
            }
            tools = {
                item["function"]["name"]
                for item in sm.get_tool_definitions(
                    run_mode=RUN_MODE_EXECUTION,
                    run_context=run_context,
                )
            }
            self.assertIn("publish_artifacts", tools)

    def test_allowed_skill_scope_filters_tool_visibility_and_search(self):
        skill_dir = os.path.join(self.skills_dir, "notes-tools")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "impl.py"), "w", encoding="utf-8") as f:
            f.write(
                "def _read_note(path):\n"
                "    return path\n\n"
                "TOOL_EXPORTS = [\n"
                "    {\n"
                "        'name': 'read_note',\n"
                "        'handler': _read_note,\n"
                "        'description': 'Read note content',\n"
                "        'parameters': {'type': 'object', 'properties': {}, 'required': []},\n"
                "        'search_hint': 'notes read text',\n"
                "    },\n"
                "]\n"
            )
        other_dir = os.path.join(self.skills_dir, "image-tools")
        os.makedirs(other_dir, exist_ok=True)
        with open(os.path.join(other_dir, "impl.py"), "w", encoding="utf-8") as f:
            f.write(
                "def _resize_image(path):\n"
                "    return path\n\n"
                "TOOL_EXPORTS = [\n"
                "    {\n"
                "        'name': 'resize_image',\n"
                "        'handler': _resize_image,\n"
                "        'description': 'Resize an image',\n"
                "        'parameters': {'type': 'object', 'properties': {}, 'required': []},\n"
                "        'search_hint': 'image resize photo',\n"
                "    },\n"
                "]\n"
            )

        sm = self._build_manager()
        run_context = {"mode": RUN_MODE_EXECUTION, "allowed_skill_names": ["notes-tools"]}
        discovered = set()

        result = sm.call_tool(
            "tool_search",
            {"query": "notes image resize"},
            context={
                "run_context": run_context,
                "discovered_tool_names": discovered,
            },
        )

        self.assertIn("read_note", result["discovered_tools"])
        self.assertNotIn("resize_image", result["discovered_tools"])

        visible = {
            item["function"]["name"]
            for item in sm.get_tool_definitions(
                run_mode=RUN_MODE_EXECUTION,
                discovered_tool_names=discovered,
                run_context=run_context,
            )
        }
        self.assertIn("tool_search", visible)
        self.assertIn("read_note", visible)
        self.assertNotIn("resize_image", visible)

        denied = sm.call_tool(
            "resize_image",
            {"path": "demo.png"},
            context={"run_context": run_context},
        )
        self.assertIn("not allowed", denied)


if __name__ == "__main__":
    unittest.main()
