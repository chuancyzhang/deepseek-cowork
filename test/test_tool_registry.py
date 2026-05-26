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

    def test_parallel_tools_visible_by_default_in_execution_mode(self):
        self._copy_repo_skill("meta-tools")
        sm = self._build_manager()

        execution_initial = {
            item["function"]["name"]
            for item in sm.get_tool_definitions(
                run_mode=RUN_MODE_EXECUTION,
                discovered_tool_names=set(),
            )
        }

        self.assertIn("tool_search", execution_initial)
        self.assertIn("parallel_tools", execution_initial)

    def test_python_runner_visible_by_default_in_execution_mode(self):
        self._copy_repo_skill("python-runner")
        sm = self._build_manager()

        execution_initial = {
            item["function"]["name"]
            for item in sm.get_tool_definitions(
                run_mode=RUN_MODE_EXECUTION,
                discovered_tool_names=set(),
            )
        }
        clarifying_initial = {
            item["function"]["name"]
            for item in sm.get_tool_definitions(
                run_mode=RUN_MODE_CLARIFYING,
                discovered_tool_names=set(),
            )
        }

        self.assertIn("run_python_code", execution_initial)
        self.assertNotIn("install_package", execution_initial)
        self.assertNotIn("run_python_code", clarifying_initial)

    def test_parallel_tools_runs_read_only_calls_and_preserves_order(self):
        self._copy_repo_skill("meta-tools")
        skill_dir = os.path.join(self.skills_dir, "notes-tools")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "impl.py"), "w", encoding="utf-8") as f:
            f.write(
                "import time\n\n"
                "def _slow_read_note(label='slow'):\n"
                "    time.sleep(0.15)\n"
                "    return {'label': label, 'speed': 'slow'}\n\n"
                "def _fast_read_note(label='fast'):\n"
                "    time.sleep(0.01)\n"
                "    return {'label': label, 'speed': 'fast'}\n\n"
                "TOOL_EXPORTS = [\n"
                "    {\n"
                "        'name': 'slow_read_note',\n"
                "        'handler': _slow_read_note,\n"
                "        'description': 'Read a note slowly',\n"
                "        'parameters': {'type': 'object', 'properties': {'label': {'type': 'string'}}, 'required': []},\n"
                "        'read_only': True,\n"
                "        'search_hint': 'notes read slow',\n"
                "    },\n"
                "    {\n"
                "        'name': 'fast_read_note',\n"
                "        'handler': _fast_read_note,\n"
                "        'description': 'Read a note quickly',\n"
                "        'parameters': {'type': 'object', 'properties': {'label': {'type': 'string'}}, 'required': []},\n"
                "        'read_only': True,\n"
                "        'search_hint': 'notes read fast',\n"
                "    },\n"
                "]\n"
            )

        sm = self._build_manager()
        discovered = set()
        sm.call_tool(
            "tool_search",
            {"query": "notes read"},
            context={
                "run_context": {"mode": RUN_MODE_EXECUTION},
                "discovered_tool_names": discovered,
            },
        )

        result = sm.call_tool(
            "parallel_tools",
            {
                "calls": [
                    {"id": "slow-1", "name": "slow_read_note", "args": {"label": "first"}},
                    {"id": "fast-1", "name": "fast_read_note", "args": {"label": "second"}},
                ],
                "max_concurrency": 2,
            },
            context={
                "run_context": {"mode": RUN_MODE_EXECUTION},
                "discovered_tool_names": discovered,
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 2)
        self.assertEqual([item["id"] for item in result["results"]], ["slow-1", "fast-1"])
        self.assertEqual([item["name"] for item in result["results"]], ["slow_read_note", "fast_read_note"])
        self.assertEqual(result["results"][0]["result"]["label"], "first")
        self.assertEqual(result["results"][1]["result"]["label"], "second")

    def test_parallel_tools_rejects_destructive_calls_without_executing_them(self):
        self._copy_repo_skill("meta-tools")
        skill_dir = os.path.join(self.skills_dir, "notes-tools")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "impl.py"), "w", encoding="utf-8") as f:
            f.write(
                "import os\n\n"
                "def _read_note(path='memo.txt'):\n"
                "    return {'path': path, 'content': 'ok'}\n\n"
                "def _write_note(path, content, workspace_dir=None):\n"
                "    marker = os.path.join(workspace_dir, 'write-note-marker.txt')\n"
                "    with open(marker, 'w', encoding='utf-8') as handle:\n"
                "        handle.write(content)\n"
                "    return {'path': path, 'written': True}\n\n"
                "TOOL_EXPORTS = [\n"
                "    {\n"
                "        'name': 'read_note',\n"
                "        'handler': _read_note,\n"
                "        'description': 'Read note content',\n"
                "        'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': []},\n"
                "        'read_only': True,\n"
                "        'search_hint': 'notes read text',\n"
                "    },\n"
                "    {\n"
                "        'name': 'write_note',\n"
                "        'handler': _write_note,\n"
                "        'description': 'Write note content',\n"
                "        'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}, 'content': {'type': 'string'}}, 'required': ['path', 'content']},\n"
                "        'destructive': True,\n"
                "        'search_hint': 'notes write text',\n"
                "    },\n"
                "]\n"
            )

        sm = self._build_manager()
        discovered = set()
        sm.call_tool(
            "tool_search",
            {"query": "notes"},
            context={
                "run_context": {"mode": RUN_MODE_EXECUTION},
                "discovered_tool_names": discovered,
            },
        )

        result = sm.call_tool(
            "parallel_tools",
            {
                "calls": [
                    {"id": "write-1", "name": "write_note", "args": {"path": "memo.txt", "content": "blocked"}},
                    {"id": "read-1", "name": "read_note", "args": {"path": "memo.txt"}},
                ],
                "max_concurrency": 2,
            },
            context={
                "run_context": {"mode": RUN_MODE_EXECUTION},
                "discovered_tool_names": discovered,
            },
        )

        marker_path = os.path.join(self.temp_dir, "write-note-marker.txt")
        self.assertEqual(result["status"], "partial_error")
        self.assertEqual(result["results"][0]["status"], "denied")
        self.assertIn("read-only tool", result["results"][0]["error"])
        self.assertEqual(result["results"][1]["status"], "ok")
        self.assertFalse(os.path.exists(marker_path))

    def test_parallel_tools_rejects_undiscovered_skill_scoped_and_mode_blocked_calls(self):
        self._copy_repo_skill("meta-tools")
        notes_dir = os.path.join(self.skills_dir, "notes-tools")
        os.makedirs(notes_dir, exist_ok=True)
        with open(os.path.join(notes_dir, "impl.py"), "w", encoding="utf-8") as f:
            f.write(
                "def _read_note(path='memo.txt'):\n"
                "    return {'path': path}\n\n"
                "def _execution_only_read(path='memo.txt'):\n"
                "    return {'path': path, 'mode': 'execution'}\n\n"
                "TOOL_EXPORTS = [\n"
                "    {\n"
                "        'name': 'read_note',\n"
                "        'handler': _read_note,\n"
                "        'description': 'Read note content',\n"
                "        'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': []},\n"
                "        'read_only': True,\n"
                "        'search_hint': 'notes read text',\n"
                "    },\n"
                "    {\n"
                "        'name': 'execution_only_read',\n"
                "        'handler': _execution_only_read,\n"
                "        'description': 'Execution-only read tool',\n"
                "        'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': []},\n"
                "        'read_only': True,\n"
                "        'allowed_modes': ['execution'],\n"
                "        'search_hint': 'notes read execution only',\n"
                "    },\n"
                "]\n"
            )
        image_dir = os.path.join(self.skills_dir, "image-tools")
        os.makedirs(image_dir, exist_ok=True)
        with open(os.path.join(image_dir, "impl.py"), "w", encoding="utf-8") as f:
            f.write(
                "def _resize_image(path='demo.png'):\n"
                "    return {'path': path, 'kind': 'image'}\n\n"
                "TOOL_EXPORTS = [\n"
                "    {\n"
                "        'name': 'resize_image',\n"
                "        'handler': _resize_image,\n"
                "        'description': 'Resize image metadata',\n"
                "        'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': []},\n"
                "        'read_only': True,\n"
                "        'search_hint': 'image resize photo',\n"
                "    },\n"
                "]\n"
            )

        sm = self._build_manager()
        discovered = set()
        sm.call_tool(
            "tool_search",
            {"query": "notes image"},
            context={
                "run_context": {"mode": RUN_MODE_EXECUTION},
                "discovered_tool_names": discovered,
            },
        )

        result = sm.call_tool(
            "parallel_tools",
            {
                "calls": [
                    {"id": "hidden", "name": "read_note", "args": {"path": "a.txt"}},
                    {"id": "scoped", "name": "resize_image", "args": {"path": "a.png"}},
                    {"id": "mode", "name": "execution_only_read", "args": {"path": "b.txt"}},
                ]
            },
            context={
                "run_context": {
                    "mode": RUN_MODE_CLARIFYING,
                    "allowed_skill_names": ["notes-tools"],
                },
                "discovered_tool_names": {"resize_image", "execution_only_read"},
            },
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual([item["status"] for item in result["results"]], ["denied", "denied", "denied"])
        self.assertIn("not been discovered", result["results"][0]["error"])
        self.assertIn("not allowed for this agent profile", result["results"][1]["error"])
        self.assertIn("not allowed in the current mode", result["results"][2]["error"])

    def test_parallel_tools_returns_partial_error_when_one_subcall_fails(self):
        self._copy_repo_skill("meta-tools")
        skill_dir = os.path.join(self.skills_dir, "notes-tools")
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "impl.py"), "w", encoding="utf-8") as f:
            f.write(
                "def _read_note(path='memo.txt'):\n"
                "    return {'path': path, 'content': 'ok'}\n\n"
                "def _boom_note(path='memo.txt'):\n"
                "    raise RuntimeError('boom')\n\n"
                "TOOL_EXPORTS = [\n"
                "    {\n"
                "        'name': 'read_note',\n"
                "        'handler': _read_note,\n"
                "        'description': 'Read note content',\n"
                "        'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': []},\n"
                "        'read_only': True,\n"
                "        'search_hint': 'notes read text',\n"
                "    },\n"
                "    {\n"
                "        'name': 'boom_note',\n"
                "        'handler': _boom_note,\n"
                "        'description': 'Raise an exception',\n"
                "        'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': []},\n"
                "        'read_only': True,\n"
                "        'search_hint': 'notes explode error',\n"
                "    },\n"
                "]\n"
            )

        sm = self._build_manager()
        discovered = set()
        sm.call_tool(
            "tool_search",
            {"query": "notes"},
            context={
                "run_context": {"mode": RUN_MODE_EXECUTION, "allowed_skill_names": ["notes-tools"]},
                "discovered_tool_names": discovered,
            },
        )

        result = sm.call_tool(
            "parallel_tools",
            {
                "calls": [
                    {"id": "ok-1", "name": "read_note", "args": {"path": "memo.txt"}},
                    {"id": "boom-1", "name": "boom_note", "args": {"path": "memo.txt"}},
                ],
                "max_concurrency": 2,
            },
            context={
                "run_context": {"mode": RUN_MODE_EXECUTION, "allowed_skill_names": ["notes-tools"]},
                "discovered_tool_names": discovered,
            },
        )

        self.assertEqual(result["status"], "partial_error")
        self.assertEqual(result["results"][0]["status"], "ok")
        self.assertEqual(result["results"][1]["status"], "error")
        self.assertIn("Error executing boom_note: boom", result["results"][1]["error"])

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
