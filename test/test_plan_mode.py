import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_mode import (
    RUN_MODE_PLANNING,
    get_planning_read_tools,
    is_tool_allowed_in_planning,
)


class _ConfigStub:
    def __init__(self, history_dir):
        self._history_dir = history_dir

    def get(self, _key, default=None):
        return default

    def get_chat_history_dir(self):
        return self._history_dir


class TestPlanModeHelpers(unittest.TestCase):
    def test_get_planning_read_tools_preserves_planning_order_and_deduplicates(self):
        available_tool_names = [
            "bash",
            "search_codebase",
            "list_files",
            "search_codebase",
            "read_file",
            "grep",
            "glob",
            "search_files",
            "read_memories",
            "write_file",
        ]

        self.assertEqual(
            get_planning_read_tools(available_tool_names),
            [
                "list_files",
                "read_file",
                "glob",
                "grep",
                "search_files",
                "search_codebase",
                "read_memories",
            ],
        )

    def test_is_tool_allowed_in_planning_supports_compat_search_aliases_only(self):
        self.assertTrue(is_tool_allowed_in_planning("search_files"))
        self.assertTrue(is_tool_allowed_in_planning("search_codebase"))
        self.assertFalse(is_tool_allowed_in_planning("bash"))
        self.assertFalse(is_tool_allowed_in_planning("write_file"))


class TestPlanningModeLLMWorker(unittest.TestCase):
    def test_planning_mode_filters_to_allowed_read_and_interaction_tools(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self):
                names = [
                    "bash",
                    "write_file",
                    "search_codebase",
                    "list_files",
                    "read_file",
                    "glob",
                    "grep",
                    "request_user_input",
                ]
                return [
                    {"type": "function", "function": {"name": name, "description": "", "parameters": {}}}
                    for name in names
                ]

            def check_for_updates(self):
                return False

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        try:
            with patch("core.agent.SkillManager", _SkillManagerStub):
                worker = LLMWorker(
                    [{"role": "user", "content": "plan this"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_PLANNING},
                )

            tool_names = {item["function"]["name"] for item in worker.tools}
            self.assertIn("search_codebase", tool_names)
            self.assertIn("list_files", tool_names)
            self.assertIn("read_file", tool_names)
            self.assertIn("glob", tool_names)
            self.assertIn("grep", tool_names)
            self.assertIn("request_user_input", tool_names)
            self.assertNotIn("bash", tool_names)
            self.assertNotIn("write_file", tool_names)
            self.assertNotIn("update_execution_plan", tool_names)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
