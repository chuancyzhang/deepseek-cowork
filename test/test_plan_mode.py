import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.plan_mode import (
    RUN_MODE_EXECUTION,
    RUN_MODE_PLANNING,
    get_planning_read_tools,
    is_tool_allowed_in_planning,
)


class _ConfigStub:
    def __init__(self, history_dir):
        self._history_dir = history_dir

    def get(self, _key, default=None):
        if _key == "api_key":
            return "test-key"
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
    def test_llm_worker_emits_system_prompt_observability_before_request(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return []

            def check_for_updates(self):
                return False

            def get_system_prompts(self, query_text=""):
                return ""

        class _ProviderStub:
            provider_name = "stub"
            model_name = "stub-model"
            base_url = ""
            thinking_enabled = False

            def __init__(self, events):
                self.events = events

            def chat_stream(self, messages, tools=None):
                self.events.append(("request", messages[0].get("content", "")))
                yield {"type": "content", "content": "done"}

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        events = []
        provider_events = []
        try:
            with (
                patch("core.agent.SkillManager", _SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=_ProviderStub(provider_events)),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "observe this"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_PLANNING},
                )
                worker.observability_signal.connect(lambda data: events.append(data))
                worker.run()

            self.assertTrue(events)
            self.assertEqual(events[0].get("type"), "system_prompt")
            system_prompt = events[0].get("content", "")
            self.assertIn("策略 [计划模式]", system_prompt)
            self.assertIn("run_python_code", system_prompt)
            self.assertIn("bash", system_prompt)
            self.assertIn("优先使用 'run_python_code'", system_prompt)
            self.assertIn("策略 [元工具导航]", system_prompt)
            self.assertIn("tool_search", system_prompt)
            self.assertIn("update_experience", system_prompt)
            self.assertIn("request_user_input", system_prompt)
            self.assertTrue(provider_events)
            self.assertEqual(system_prompt, provider_events[0][1])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_llm_worker_emits_tool_call_and_result_observability(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return [
                    {"type": "function", "function": {"name": "read_file", "description": "", "parameters": {}}}
                ]

            def check_for_updates(self):
                return False

            def get_system_prompts(self, query_text=""):
                return ""

            def get_skill_of_tool(self, name):
                return None

            def call_tool(self, name, args, context=None):
                return {"content": f"{name}:{args.get('path')}", "ok": True}

        class _ProviderStub:
            provider_name = "stub"
            model_name = "stub-model"
            base_url = ""
            thinking_enabled = False

            def __init__(self):
                self.turn = 0

            def chat_stream(self, messages, tools=None):
                self.turn += 1
                if self.turn == 1:
                    yield {
                        "type": "tool_call",
                        "index": 0,
                        "id": "call_1",
                        "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'},
                    }
                else:
                    yield {"type": "content", "content": "done"}

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        events = []
        provider = _ProviderStub()
        try:
            with (
                patch("core.agent.SkillManager", _SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=provider),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "read a file"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION},
                )
                worker.observability_signal.connect(lambda data: events.append(data))
                worker.run()

            calls = [event for event in events if event.get("type") == "tool_call"]
            results = [event for event in events if event.get("type") == "tool_result"]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].get("name"), "read_file")
            self.assertEqual(calls[0].get("args"), {"path": "a.txt"})
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].get("id"), "call_1")
            self.assertIn("duration", results[0].get("meta", {}))
            self.assertEqual(results[0].get("result_obj", {}).get("content"), "read_file:a.txt")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

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

    def test_llm_worker_exposes_deferred_tools_only_after_discovery(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, run_mode=None, discovered_tool_names=None, include_deferred=False):
                discovered = set(discovered_tool_names or [])
                names = ["tool_search", "read_file"]
                if run_mode == RUN_MODE_EXECUTION and "write_file" in discovered:
                    names.append("write_file")
                return [
                    {"type": "function", "function": {"name": name, "description": "", "parameters": {}}}
                    for name in names
                ]

            def is_tool_allowed(self, name, run_mode):
                if run_mode == RUN_MODE_PLANNING and name == "write_file":
                    return False
                return True

            def is_tool_visible(self, name, run_mode, discovered_tool_names=None):
                if name in {"tool_search", "read_file"}:
                    return True
                return name in set(discovered_tool_names or [])

            def check_for_updates(self):
                return False

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        try:
            with patch("core.agent.SkillManager", _SkillManagerStub):
                worker = LLMWorker(
                    [{"role": "user", "content": "edit this"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION},
                )

            initial_names = {item["function"]["name"] for item in worker.tools}
            self.assertIn("tool_search", initial_names)
            self.assertIn("read_file", initial_names)
            self.assertNotIn("write_file", initial_names)

            worker.discovered_tool_names.add("write_file")
            worker._refresh_tool_definitions()
            after_discovery = {item["function"]["name"] for item in worker.tools}
            self.assertIn("write_file", after_discovery)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_llm_worker_keeps_planning_hard_boundary_after_discovery(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, run_mode=None, discovered_tool_names=None, include_deferred=False):
                names = ["tool_search", "read_file", "write_file"]
                return [
                    {"type": "function", "function": {"name": name, "description": "", "parameters": {}}}
                    for name in names
                ]

            def is_tool_allowed(self, name, run_mode):
                return not (run_mode == RUN_MODE_PLANNING and name == "write_file")

            def is_tool_visible(self, name, run_mode, discovered_tool_names=None):
                return True

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
            self.assertIn("tool_search", tool_names)
            self.assertIn("read_file", tool_names)
            self.assertNotIn("write_file", tool_names)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
