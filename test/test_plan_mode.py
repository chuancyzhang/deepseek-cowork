import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.clarify_mode import (
    OFFICE_OUTPUT_PROFILE_PPT,
    RUN_MODE_CLARIFYING,
    RUN_MODE_EXECUTION,
    WORKFLOW_MODE_OFFICE_HTML_FIRST,
    get_clarifying_read_tools,
    is_tool_allowed_in_clarifying,
    normalize_selected_skill_names,
    normalize_run_context,
)
from core.sop_manager import create_sop_run


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
                "workflow_mode": WORKFLOW_MODE_OFFICE_HTML_FIRST,
                "office_output_profile": OFFICE_OUTPUT_PROFILE_PPT,
            }
        )

        self.assertEqual(ctx["mode"], RUN_MODE_CLARIFYING)
        self.assertEqual(ctx["selected_model_id"], "openai-fast")
        self.assertEqual(ctx["im_provider"], "feishu")
        self.assertEqual(ctx["channel"], "feishu")
        self.assertEqual(ctx["workflow_mode"], WORKFLOW_MODE_OFFICE_HTML_FIRST)
        self.assertEqual(ctx["office_output_profile"], OFFICE_OUTPUT_PROFILE_PPT)

    def test_normalize_selected_skill_names_deduplicates_and_filters_blanks(self):
        self.assertEqual(
            normalize_selected_skill_names([" browser ", "", None, "browser", "python-runner"]),
            ["browser", "python-runner"],
        )

    def test_get_clarifying_read_tools_preserves_order_and_deduplicates(self):
        available_tool_names = [
            "bash",
            "search_codebase",
            "workspace_list_files",
            "search_codebase",
            "text_file_read",
            "grep",
            "glob",
            "search_files",
            "read_memories",
            "text_file_write",
        ]

        self.assertEqual(
            get_clarifying_read_tools(available_tool_names),
            [
                "workspace_list_files",
                "text_file_read",
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
        self.assertFalse(is_tool_allowed_in_clarifying("text_file_write"))

    def test_normalize_run_context_preserves_sop_run(self):
        sop_run = create_sop_run(
            {
                "id": "office",
                "name": "Office",
                "steps": [{"title": "Step 1", "instructions": "Do it"}],
            }
        )

        ctx = normalize_run_context(
            {
                "mode": RUN_MODE_EXECUTION,
                "selected_skill_names": ["browser-automation"],
                "sop_run": sop_run,
            }
        )

        self.assertEqual(ctx["sop_run"]["template_id"], "office")
        self.assertEqual(ctx["sop_run"]["steps"][0]["title"], "Step 1")
        self.assertEqual(ctx["selected_skill_names"], ["browser-automation"])

    def test_normalize_run_context_defaults_invalid_office_profile_to_free(self):
        ctx = normalize_run_context(
            {
                "mode": RUN_MODE_EXECUTION,
                "workflow_mode": "unknown",
                "office_output_profile": "slides",
            }
        )

        self.assertEqual(ctx["workflow_mode"], "")
        self.assertEqual(ctx["office_output_profile"], "free")


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
                system_messages = [msg.get("content", "") for msg in messages if msg.get("role") == "system"]
                self.events.append(("request", system_messages))
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
            runtime_prompt = events[0].get("runtime_context", "")
            self.assertIn("策略 [反问模式]", runtime_prompt)
            self.assertIn("request_user_input", runtime_prompt)
            self.assertNotIn("策略 [计划模式]", runtime_prompt)
            self.assertNotIn("<proposed_plan>", runtime_prompt)
            self.assertTrue(provider_events)
            self.assertEqual(system_prompt, provider_events[0][1][0])
            self.assertEqual(runtime_prompt, provider_events[0][1][-1])
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
                system_messages = [msg.get("content", "") for msg in messages if msg.get("role") == "system"]
                self.events.append(("request", system_messages))
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

            runtime_prompt = events[0].get("runtime_context", "")
            self.assertIn("# 用户指定能力", runtime_prompt)
            self.assertIn("`browser-automation`: 浏览器自动化", runtime_prompt)
            self.assertIn("Browser automation brief", runtime_prompt)
            self.assertNotIn("General skill prompt", runtime_prompt)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_llm_worker_includes_sop_step_prompt(self):
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
                system_messages = [msg.get("content", "") for msg in messages if msg.get("role") == "system"]
                self.events.append(("request", system_messages))
                yield {"type": "content", "content": "done"}

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        events = []
        provider_events = []
        sop_run = create_sop_run(
            {
                "id": "office",
                "name": "Office",
                "description": "Handle office work",
                "steps": [{"title": "Step 1", "instructions": "Only do step 1", "success_criteria": "Step 1 is done"}],
            }
        )
        try:
            with (
                patch("core.agent.SkillManager", _SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=_ProviderStub(provider_events)),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "do the task"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION, "sop_run": sop_run},
                )
                worker.observability_signal.connect(lambda data: events.append(data))
                worker.run()

            system_prompt = events[0].get("content", "")
            runtime_prompt = events[0].get("runtime_context", "")
            self.assertIn("# SOP 当前步骤", runtime_prompt)
            self.assertIn("当前 SOP: Office", runtime_prompt)
            self.assertIn("本轮只允许完成当前步骤", runtime_prompt)
            self.assertEqual(system_prompt, provider_events[0][1][0])
            self.assertEqual(runtime_prompt, provider_events[0][1][-1])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_llm_worker_includes_office_mode_prompt(self):
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
                system_messages = [msg.get("content", "") for msg in messages if msg.get("role") == "system"]
                self.events.append(("request", system_messages))
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
                    [{"role": "user", "content": "make slides"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={
                        "mode": RUN_MODE_EXECUTION,
                        "workflow_mode": WORKFLOW_MODE_OFFICE_HTML_FIRST,
                        "office_output_profile": OFFICE_OUTPUT_PROFILE_PPT,
                    },
                )
                worker.observability_signal.connect(lambda data: events.append(data))
                worker.run()

            runtime_prompt = events[0].get("runtime_context", "")
            self.assertIn("策略 [办公稿生成]", runtime_prompt)
            self.assertIn("当前类型: PPT", runtime_prompt)
            self.assertIn("不要称为 HTML 模式", runtime_prompt)
            self.assertIn("继续生成 PPTX", runtime_prompt)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_llm_worker_ignores_tool_calls_without_function_name(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return [{"type": "function", "function": {"name": "tool_search", "description": "", "parameters": {}}}]

            def is_tool_allowed(self, _name, _run_mode):
                return True

            def is_tool_visible(self, _name, _run_mode, discovered_tool_names=None, run_context=None):
                return True

            def check_for_updates(self):
                return False

            def get_skill_of_tool(self, _tool_name):
                return None

            def get_brief_skill_prompt(self, _skill_name):
                return ""

            def get_system_prompts(self, query_text="", limit=6, preferred_skill_names=None, exclude_skill_names=None):
                return ""

        class _ProviderStub:
            provider_name = "stub"
            model_name = "stub-model"
            base_url = ""
            thinking_enabled = False

            def __init__(self):
                self.calls = 0

            def chat_stream(self, messages, tools=None):
                self.calls += 1
                if self.calls == 1:
                    yield {
                        "type": "tool_call",
                        "index": 0,
                        "id": "bad-tool-1",
                        "function": {"arguments": "{\"query\": \"document pdf docx\"}"},
                    }
                    return
                yield {"type": "content", "content": "direct answer"}

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        results = []
        try:
            provider = _ProviderStub()
            with (
                patch("core.agent.SkillManager", _SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=provider),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "read this screenshot"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION},
                )
                worker.finished_signal.connect(lambda data: results.append(data))
                worker.run()

            self.assertEqual(provider.calls, 2)
            self.assertTrue(results)
            result = results[0]
            self.assertEqual(result["content"], "direct answer")
            assistant_messages = [msg for msg in result["generated_messages"] if msg.get("role") == "assistant"]
            self.assertTrue(assistant_messages)
            self.assertFalse(any(msg.get("tool_calls") for msg in assistant_messages))
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

    def test_llm_worker_refreshes_system_prompt_after_tool_search_discovery(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                discovered = set(kwargs.get("discovered_tool_names") or [])
                names = ["tool_search", "run_python_code"]
                if "bash" in discovered:
                    names.append("bash")
                return [
                    {"type": "function", "function": {"name": name, "description": "", "parameters": {}}}
                    for name in names
                ]

            def is_tool_allowed(self, _name, _run_mode):
                return True

            def is_tool_visible(self, name, _run_mode, discovered_tool_names=None, run_context=None):
                if name in {"tool_search", "run_python_code"}:
                    return True
                discovered = set(discovered_tool_names or [])
                return name in discovered

            def call_tool(self, name, args, context=None):
                if name != "tool_search":
                    return {"status": "error", "message": f"unexpected tool {name}"}
                discovered = (context or {}).get("discovered_tool_names")
                if hasattr(discovered, "update"):
                    discovered.update(["bash"])
                return {
                    "status": "ok",
                    "discovered_tools": ["bash"],
                    "message": "Matched tools will be available on the next model turn.",
                }

            def check_for_updates(self):
                return False

            def get_skill_of_tool(self, _tool_name):
                return None

            def get_brief_skill_prompt(self, _skill_name):
                return ""

            def get_system_prompts(self, *args, **kwargs):
                return ""

        class _ProviderStub:
            def __init__(self, calls):
                self.calls = calls
                self.provider_name = "test-provider"
                self.model_name = "test-model"
                self.base_url = ""
                self.thinking_enabled = False

            def chat_stream(self, messages, tools=None):
                tool_names = [(item.get("function") or {}).get("name") for item in (tools or [])]
                system_messages = [
                    item.get("content", "")
                    for item in messages
                    if isinstance(item, dict) and item.get("role") == "system"
                ]
                runtime_prompt = system_messages[-1] if system_messages else ""
                self.calls.append({"tool_names": tool_names, "system_prompt": runtime_prompt})
                if len(self.calls) == 1:
                    yield {
                        "type": "tool_call",
                        "index": 0,
                        "id": "tool-search-1",
                        "function": {"name": "tool_search", "arguments": "{\"query\": \"python bash\"}"},
                    }
                    return
                yield {"type": "content", "content": "done"}

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        provider_calls = []
        try:
            with (
                patch("core.agent.SkillManager", _SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=_ProviderStub(provider_calls)),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "need execution tools"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION},
                )
                worker.run()

            self.assertEqual(len(provider_calls), 2)
            self.assertEqual(provider_calls[0]["tool_names"], ["tool_search", "run_python_code"])
            self.assertIn("- `tool_search`, `run_python_code`", provider_calls[0]["system_prompt"])
            self.assertNotIn("- `tool_search`, `run_python_code`, `bash`", provider_calls[0]["system_prompt"])
            self.assertIn("run_python_code", provider_calls[1]["tool_names"])
            self.assertIn("bash", provider_calls[1]["tool_names"])
            self.assertIn("- `tool_search`, `run_python_code`, `bash`", provider_calls[1]["system_prompt"])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_llm_worker_does_not_inject_full_prompt_from_query_match(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return []

            def check_for_updates(self):
                return False

            def get_skill_of_tool(self, _tool_name):
                return None

            def get_system_prompts(self, *args, **kwargs):
                return "[Experience Package] claim-expert\nscripts: validate_input"

            def get_full_disclosure_skill_names(self, query_text=None, **_kwargs):
                return ["claim-expert"] if "claim" in str(query_text or "").lower() else []

            def get_full_skill_prompt(self, skill_name, include_references=False, include_entries=False):
                if skill_name != "claim-expert":
                    return ""
                return (
                    "## Skill Scripts\n"
                    "- `validate_input` -> `scripts/validate_input.py` (python)\n"
                    "Use `command-tools.run_skill_script` to execute these scripts inside the sandbox runtime."
                )

        class _ProviderStub:
            def __init__(self, calls):
                self.calls = calls
                self.provider_name = "test-provider"
                self.model_name = "test-model"
                self.base_url = ""
                self.thinking_enabled = False

            def chat_stream(self, messages, tools=None):
                self.calls.append(
                    {
                        "system_messages": [
                            item.get("content", "")
                            for item in messages
                            if isinstance(item, dict) and item.get("role") == "system"
                        ]
                    }
                )
                yield {"type": "content", "content": "done"}

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        provider_calls = []
        finished_payloads = []
        try:
            with (
                patch("core.agent.SkillManager", _SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=_ProviderStub(provider_calls)),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "please use the claim expert flow"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION},
                )
                worker.finished_signal.connect(lambda payload: finished_payloads.append(payload))
                worker.run()

            self.assertEqual(len(provider_calls), 1)
            combined = "\n\n".join(provider_calls[0]["system_messages"])
            self.assertNotIn("validate_input", combined)
            generated = finished_payloads[0]["generated_messages"]
            skill_contexts = [
                msg for msg in generated
                if isinstance(msg, dict)
                and isinstance(msg.get("meta"), dict)
                and msg["meta"].get("kind") == "skill_context"
            ]
            self.assertEqual(skill_contexts, [])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_llm_worker_injects_full_prompt_after_tool_search_skill_match(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return [
                    {"type": "function", "function": {"name": "tool_search", "description": "", "parameters": {}}}
                ]

            def is_tool_allowed(self, _name, _run_mode):
                return True

            def is_tool_visible(self, _name, _run_mode, discovered_tool_names=None, run_context=None):
                return True

            def call_tool(self, name, args, context=None):
                if name != "tool_search":
                    return {"status": "error", "message": f"unexpected tool {name}"}
                return {
                    "status": "ok",
                    "discovered_tools": [],
                    "skills": [
                        {
                            "name": "claim-expert",
                            "prompt_level": "full",
                            "preferred_tool": "run_skill_script",
                        }
                    ],
                    "message": "Matched tools will be available on the next model turn.",
                }

            def check_for_updates(self):
                return False

            def get_skill_of_tool(self, _tool_name):
                return None

            def get_system_prompts(self, *args, **kwargs):
                return ""

            def get_full_disclosure_skill_names(self, query_text=None, **_kwargs):
                return []

            def get_full_skill_prompt(self, skill_name, include_references=False, include_entries=False):
                if skill_name != "claim-expert":
                    return ""
                return (
                    "## Skill Scripts\n"
                    "- `validate_input` -> `scripts/validate_input.py` (python)\n"
                    "Use `command-tools.run_skill_script` to execute these scripts inside the sandbox runtime."
                )

        class _ProviderStub:
            def __init__(self, calls):
                self.calls = calls
                self.provider_name = "test-provider"
                self.model_name = "test-model"
                self.base_url = ""
                self.thinking_enabled = False

            def chat_stream(self, messages, tools=None):
                self.calls.append(
                    {
                        "system_messages": [
                            item.get("content", "")
                            for item in messages
                            if isinstance(item, dict) and item.get("role") == "system"
                        ]
                    }
                )
                if len(self.calls) == 1:
                    yield {
                        "type": "tool_call",
                        "index": 0,
                        "id": "tool-search-1",
                        "function": {"name": "tool_search", "arguments": "{\"query\": \"claim expert\"}"},
                    }
                    return
                yield {"type": "content", "content": "done"}

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        provider_calls = []
        try:
            with (
                patch("core.agent.SkillManager", _SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=_ProviderStub(provider_calls)),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "help me find the right claim skill"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION},
                )
                worker.run()

            self.assertEqual(len(provider_calls), 2)
            self.assertNotIn("validate_input", "\n\n".join(provider_calls[0]["system_messages"]))
            self.assertIn("validate_input", "\n\n".join(provider_calls[1]["system_messages"]))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_skill_context_injection_deduplicates_by_content_hash(self):
        class _SkillManagerStub:
            def get_full_skill_prompt(self, skill_name, include_references=False, include_entries=False):
                return f"full prompt for {skill_name}"

        class _Signal:
            def __init__(self):
                self.events = []

            def emit(self, payload):
                self.events.append(payload)

        from core.agent import LLMWorker

        worker = LLMWorker.__new__(LLMWorker)
        worker.skill_manager = _SkillManagerStub()
        worker.observability_signal = _Signal()
        current_messages = [{"role": "user", "content": "hello"}]
        generated_messages = []
        disclosed = set()

        worker._append_skill_prompts_for_names(
            ["claim-expert"],
            current_messages,
            disclosed,
            generated_messages,
            source="skill_prompt_query_match",
        )
        worker._append_skill_prompts_for_names(
            ["claim-expert"],
            current_messages,
            disclosed,
            generated_messages,
            source="skill_prompt_query_match",
        )

        skill_contexts = [
            msg for msg in current_messages
            if isinstance(msg, dict)
            and isinstance(msg.get("meta"), dict)
            and msg["meta"].get("kind") == "skill_context"
        ]
        self.assertEqual(len(skill_contexts), 1)
        self.assertEqual(len(generated_messages), 1)
        self.assertEqual(skill_contexts[0]["meta"]["skill_name"], "claim-expert")
        self.assertTrue(skill_contexts[0]["meta"]["content_hash"])

    def test_clarifying_mode_filters_to_allowed_read_and_interaction_tools(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self):
                names = [
                    "bash",
                    "text_file_write",
                    "search_codebase",
                    "workspace_list_files",
                    "text_file_read",
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
            self.assertIn("workspace_list_files", tool_names)
            self.assertIn("text_file_read", tool_names)
            self.assertIn("glob", tool_names)
            self.assertIn("grep", tool_names)
            self.assertIn("request_user_input", tool_names)
            self.assertNotIn("bash", tool_names)
            self.assertNotIn("text_file_write", tool_names)
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
                names = ["tool_search", "text_file_read", "text_file_write"]
                return [
                    {"type": "function", "function": {"name": name, "description": "", "parameters": {}}}
                    for name in names
                ]

            def is_tool_allowed(self, name, run_mode):
                return not (run_mode == RUN_MODE_CLARIFYING and name == "text_file_write")

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
            self.assertIn("text_file_read", tool_names)
            self.assertNotIn("text_file_write", tool_names)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

