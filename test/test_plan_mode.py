import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.clarify_mode import (
    RUN_MODE_CLARIFYING,
    RUN_MODE_EXECUTION,
    get_clarifying_read_tools,
    is_tool_allowed_in_clarifying,
    normalize_selected_skill_names,
    normalize_run_context,
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


class TestClarifyModeHelpers(unittest.TestCase):
    def test_normalize_run_context_maps_legacy_planning_to_clarifying(self):
        ctx = normalize_run_context(
            {
                "mode": "planning",
                "selected_model_id": "openai-fast",
                "im_provider": "feishu",
                "channel": "feishu",
            }
        )

        self.assertEqual(ctx["mode"], RUN_MODE_CLARIFYING)
        self.assertEqual(ctx["selected_model_id"], "openai-fast")
        self.assertEqual(ctx["im_provider"], "feishu")
        self.assertEqual(ctx["channel"], "feishu")

    def test_normalize_selected_skill_names_deduplicates_and_filters_blanks(self):
        self.assertEqual(
            normalize_selected_skill_names([" browser ", "", None, "browser", "python-runner"]),
            ["browser", "python-runner"],
        )

    def test_get_clarifying_read_tools_preserves_order_and_deduplicates(self):
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
            get_clarifying_read_tools(available_tool_names),
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

    def test_is_tool_allowed_in_clarifying_supports_read_and_question_tools_only(self):
        self.assertTrue(is_tool_allowed_in_clarifying("search_files"))
        self.assertTrue(is_tool_allowed_in_clarifying("search_codebase"))
        self.assertTrue(is_tool_allowed_in_clarifying("request_user_input"))
        self.assertFalse(is_tool_allowed_in_clarifying("bash"))
        self.assertFalse(is_tool_allowed_in_clarifying("write_file"))


class TestClarifyModeLLMWorker(unittest.TestCase):
    def test_llm_worker_emits_clarifying_system_prompt(self):
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
                    [{"role": "user", "content": "clarify this"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_CLARIFYING},
                )
                worker.observability_signal.connect(lambda data: events.append(data))
                worker.run()

            self.assertTrue(events)
            system_prompt = events[0].get("content", "")
            self.assertIn("策略 [反问模式]", system_prompt)
            self.assertIn("request_user_input", system_prompt)
            self.assertNotIn("策略 [计划模式]", system_prompt)
            self.assertNotIn("<proposed_plan>", system_prompt)
            self.assertTrue(provider_events)
            self.assertEqual(system_prompt, provider_events[0][1])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_llm_worker_includes_user_selected_skills_in_system_prompt(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return []

            def check_for_updates(self):
                return False

            def get_brief_skill_prompt(self, skill_name):
                if skill_name == "browser-automation":
                    return "Browser automation brief"
                return ""

            def get_skill_display_name(self, skill_name):
                if skill_name == "browser-automation":
                    return "浏览器自动化"
                return skill_name

            def get_system_prompts(self, query_text="", limit=6, preferred_skill_names=None, exclude_skill_names=None):
                return "General skill prompt"

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
                    [{"role": "user", "content": "open the site"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION, "selected_skill_names": ["browser-automation"]},
                )
                worker.observability_signal.connect(lambda data: events.append(data))
                worker.run()

            system_prompt = events[0].get("content", "")
            self.assertIn("# 用户指定能力", system_prompt)
            self.assertIn("`browser-automation`: 浏览器自动化", system_prompt)
            self.assertIn("Browser automation brief", system_prompt)
            self.assertIn("General skill prompt", system_prompt)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_selected_skill_tools_are_visible_without_tool_search(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tools_for_skill(self, skill_name):
                if skill_name == "browser-automation":
                    return ["open_browser_tab"]
                return []

            def get_tool_definitions(self, *args, **kwargs):
                discovered = set(kwargs.get("discovered_tool_names") or [])
                names = ["open_browser_tab"] if "open_browser_tab" in discovered else []
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
                    [{"role": "user", "content": "browse"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION, "selected_skill_names": ["browser-automation"]},
                )

            tool_names = {item["function"]["name"] for item in worker.tools}
            self.assertIn("open_browser_tab", tool_names)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_clarifying_mode_filters_to_allowed_read_and_interaction_tools(self):
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
                    [{"role": "user", "content": "clarify this"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_CLARIFYING},
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
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_llm_worker_accepts_legacy_planning_context_as_clarifying(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return []

            def check_for_updates(self):
                return False

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        try:
            with patch("core.agent.SkillManager", _SkillManagerStub):
                worker = LLMWorker(
                    [{"role": "user", "content": "legacy"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": "planning"},
                )

            self.assertEqual(worker.run_context["mode"], RUN_MODE_CLARIFYING)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_llm_worker_keeps_clarifying_hard_boundary_after_discovery(self):
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
                return not (run_mode == RUN_MODE_CLARIFYING and name == "write_file")

            def is_tool_visible(self, name, run_mode, discovered_tool_names=None):
                return True

            def check_for_updates(self):
                return False

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        try:
            with patch("core.agent.SkillManager", _SkillManagerStub):
                worker = LLMWorker(
                    [{"role": "user", "content": "clarify this"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_CLARIFYING},
                )

            tool_names = {item["function"]["name"] for item in worker.tools}
            self.assertIn("tool_search", tool_names)
            self.assertIn("read_file", tool_names)
            self.assertNotIn("write_file", tool_names)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
