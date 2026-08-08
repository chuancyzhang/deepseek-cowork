import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.clarify_mode import (
    GRILL_CHECKPOINT_PURPOSE,
    GRILL_MAX_ROUNDS,
    OFFICE_OUTPUT_PROFILE_PPT,
    RUN_MODE_EXECUTION,
    RUN_MODE_GRILLING,
    WORKFLOW_MODE_OFFICE_HTML_FIRST,
    normalize_selected_skill_names,
    normalize_run_context,
)
from core.ppt_agent import (
    PPT_AGENT_PREFERENCE_BUSINESS,
    PPT_AGENT_STRATEGY_HUASHU,
    build_ppt_agent_prompt,
    choose_ppt_agent_strategy,
)
from core.message_persistence import fold_skill_state_events


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
    def test_normalize_run_context_maps_legacy_planning_to_execution(self):
        ctx = normalize_run_context(
            {
                "mode": "planning",
                "selected_model_id": "openai-fast",
                "selected_model_profile": {
                    "id": "openai-fast",
                    "provider_type": "openai",
                    "model_name": "fast-model",
                },
                "im_provider": "feishu",
                "channel": "feishu",
                "workflow_mode": WORKFLOW_MODE_OFFICE_HTML_FIRST,
                "office_output_profile": OFFICE_OUTPUT_PROFILE_PPT,
            }
        )

        self.assertEqual(ctx["mode"], RUN_MODE_EXECUTION)
        self.assertEqual(ctx["selected_model_id"], "openai-fast")
        self.assertEqual(ctx["selected_model_profile"]["model_name"], "fast-model")
        self.assertEqual(ctx["im_provider"], "feishu")
        self.assertEqual(ctx["channel"], "feishu")
        self.assertEqual(ctx["workflow_mode"], WORKFLOW_MODE_OFFICE_HTML_FIRST)
        self.assertEqual(ctx["office_output_profile"], OFFICE_OUTPUT_PROFILE_PPT)
        self.assertEqual(ctx["grill_round_count"], 0)
        self.assertEqual(ctx["grill_cycle_count"], 0)
        self.assertFalse(ctx["grill_execution_confirmed"])

    def test_normalize_selected_skill_names_deduplicates_and_filters_blanks(self):
        self.assertEqual(
            normalize_selected_skill_names([" browser ", "", None, "browser", "python-runner"]),
            ["browser", "python-runner"],
        )

    def test_normalize_run_context_discards_legacy_sop_run(self):
        ctx = normalize_run_context(
            {
                "mode": RUN_MODE_EXECUTION,
                "selected_skill_names": ["browser-automation"],
                "sop_run": {"template_id": "office", "steps": [{"title": "Step 1"}]},
            }
        )

        self.assertNotIn("sop_run", ctx)
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


class TestAppendOnlySkillStateLedger(unittest.TestCase):
    def test_skill_enable_disable_reenable_upgrade_and_duplicate_events(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                self.catalog_revision = 1
                self.prompts = {"demo-skill": "# Demo Skill v1"}

            def get_tool_definitions(self, *args, **kwargs):
                return []

            def get_tools_for_skill(self, _skill_name):
                return []

            def get_full_skill_prompt(self, skill_name):
                return self.prompts.get(skill_name, "")

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        try:
            with patch("core.agent.SkillManager", _SkillManagerStub):
                worker = LLMWorker(
                    [],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION},
                )
            messages = []
            generated = []

            first = worker._append_skill_prompts_for_names(
                ["demo-skill"], messages, set(), generated, source="selected_skill_prompt"
            )
            self.assertEqual(len(first), 1)
            first_hash = first[0]["meta"]["content_hash"]
            self.assertEqual(first[0]["meta"]["kind"], "skill_context")
            self.assertEqual(first[0]["meta"]["state"], "enabled")
            self.assertEqual(first[0]["meta"]["catalog_revision"], 1)

            duplicate = worker._append_skill_prompts_for_names(
                ["demo-skill"], messages, set(), generated, source="selected_skill_prompt"
            )
            self.assertEqual(duplicate, [])

            disabled = worker._append_skill_state_message(
                "demo-skill", "disabled", first_hash, "selected_skill_removed",
                messages, generated, selection_scoped=True,
            )
            self.assertEqual(disabled["meta"]["state"], "disabled")
            self.assertIsNone(worker._append_skill_state_message(
                "demo-skill", "disabled", first_hash, "selected_skill_removed",
                messages, generated, selection_scoped=True,
            ))

            reenabled = worker._append_skill_prompts_for_names(
                ["demo-skill"], messages, set(), generated, source="selected_skill_prompt"
            )
            self.assertEqual(len(reenabled), 1)
            self.assertEqual(reenabled[0]["meta"]["kind"], "skill_state_update")
            self.assertEqual(reenabled[0]["meta"]["content_hash"], first_hash)

            worker.skill_manager.catalog_revision = 2
            worker.skill_manager.prompts["demo-skill"] = "# Demo Skill v2"
            upgraded = worker._append_skill_prompts_for_names(
                ["demo-skill"], messages, set(), generated, source="skill_catalog_updated"
            )
            self.assertEqual(len(upgraded), 1)
            self.assertEqual(upgraded[0]["meta"]["kind"], "skill_context")
            self.assertEqual(upgraded[0]["meta"]["supersedes_hash"], first_hash)
            self.assertNotEqual(upgraded[0]["meta"]["content_hash"], first_hash)

            self.assertEqual(messages, generated)
            self.assertEqual(messages[0]["content"], "# Demo Skill v1")
            folded = fold_skill_state_events(messages)
            self.assertEqual(folded["demo-skill"]["state"], "enabled")
            self.assertEqual(folded["demo-skill"]["catalog_revision"], 2)
            self.assertEqual(
                folded["demo-skill"]["content_hash"],
                upgraded[0]["meta"]["content_hash"],
            )
            worker.chat_storage.save_conversation(
                "skill-ledger-restart",
                messages,
                title="skill ledger",
            )
            restored = worker.chat_storage.get_messages("skill-ledger-restart")
            self.assertEqual(
                [message["id"] for message in restored],
                [message["id"] for message in messages],
            )
            self.assertEqual(fold_skill_state_events(restored), folded)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_ppt_agent_strategy_rules_select_huashu_for_business_visuals(self):
        self.assertEqual(
            choose_ppt_agent_strategy("生成一份高级感路演 BP", preference=PPT_AGENT_PREFERENCE_BUSINESS),
            PPT_AGENT_STRATEGY_HUASHU,
        )
        prompt = build_ppt_agent_prompt("生成一份高级感路演 BP")
        self.assertIn("PPT Agent", prompt["prompt"])
        self.assertIn("Huashu Design", prompt["prompt"])
        self.assertIn("HTML deliverable", prompt["prompt"])


class TestClarifyModeLLMWorker(unittest.TestCase):
    def test_llm_worker_emits_necessary_clarification_policy(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return []

            def check_for_updates(self):
                return False

            def get_system_prompts(self, query_text=""):
                return ""

            def get_brief_skill_prompt(self, skill_name):
                return "Huashu Design brief" if skill_name == "huashu-design" else ""

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
                patch(
                    "core.agent.get_runtime_snapshot",
                    return_value={
                        "python": {"available": True, "version": "Python 3.test", "path": "python.exe"},
                        "node": {"available": False, "version": "", "path": ""},
                        "bash": {"available": False, "version": "", "path": ""},
                    },
                ) as runtime_snapshot_mock,
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "clarify this"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION, "clarify_round_count": 2},
                )
                worker.observability_signal.connect(lambda data: events.append(data))
                worker.run()

            self.assertTrue(events)
            prompt_event = next(event for event in events if event.get("type") == "system_prompt")
            system_prompt = prompt_event.get("content", "")
            runtime_prompt = prompt_event.get("runtime_context", "")
            self.assertIn("策略 [必要澄清]", system_prompt)
            self.assertIn("默认直接执行用户任务", system_prompt)
            self.assertNotIn("当前任务已澄清 2/3 轮", system_prompt)
            self.assertIn("request_user_input", system_prompt)
            self.assertNotIn("当前任务已澄清", runtime_prompt)
            self.assertNotIn("策略 [反问模式]", runtime_prompt)
            self.assertNotIn("<proposed_plan>", runtime_prompt)
            self.assertTrue(provider_events)
            self.assertEqual(system_prompt, provider_events[0][1][0])
            self.assertEqual(runtime_prompt, provider_events[0][1][-1])
            runtime_snapshot_mock.assert_called_once_with()
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

            prompt_event = next(event for event in events if event.get("type") == "system_prompt")
            runtime_prompt = prompt_event.get("runtime_context", "")
            self.assertIn("# 用户指定能力", runtime_prompt)
            self.assertIn("`browser-automation`: 浏览器自动化", runtime_prompt)
            self.assertIn("Browser automation brief", runtime_prompt)
            self.assertNotIn("General skill prompt", runtime_prompt)
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

            prompt_event = next(event for event in events if event.get("type") == "system_prompt")
            runtime_prompt = prompt_event.get("runtime_context", "")
            self.assertIn("策略 [办公稿生成]", runtime_prompt)
            self.assertIn("当前类型: PPT", runtime_prompt)
            self.assertIn("不要称为 HTML 模式", runtime_prompt)
            self.assertIn("继续生成 PPTX", runtime_prompt)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_llm_worker_includes_ppt_agent_strategy_prompt(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return []

            def check_for_updates(self):
                return False

            def get_system_prompts(self, query_text=""):
                return ""

            def get_brief_skill_prompt(self, skill_name):
                return "Huashu Design brief" if skill_name == "huashu-design" else ""

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
                        "ppt_agent_mode": True,
                        "ppt_agent_strategy": PPT_AGENT_STRATEGY_HUASHU,
                        "ppt_agent_selected_strategy": PPT_AGENT_STRATEGY_HUASHU,
                        "ppt_agent_preference": PPT_AGENT_PREFERENCE_BUSINESS,
                    },
                )
                worker.observability_signal.connect(lambda data: events.append(data))
                worker.run()

            prompt_event = next(event for event in events if event.get("type") == "system_prompt")
            runtime_prompt = prompt_event.get("runtime_context", "")
            self.assertIn("策略 [PPT Agent]", runtime_prompt)
            self.assertIn("Huashu Design", runtime_prompt)
            self.assertIn("HTML deliverable preview", runtime_prompt)
            self.assertIn("HTML→PPTX/DOCX/PDF", runtime_prompt)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_llm_worker_injects_selected_skill_prompt_for_ppt_agent(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return []

            def check_for_updates(self):
                return False

            def get_system_prompts(self, query_text=""):
                return ""

            def get_brief_skill_prompt(self, skill_name):
                return "Huashu Design brief" if skill_name == "huashu-design" else ""

            def get_skill_display_name(self, skill_name):
                return "Huashu Design" if skill_name == "huashu-design" else skill_name

            def get_full_skill_prompt(self, skill_name):
                return "# Huashu Design Full Skill\nUse HTML as design medium." if skill_name == "huashu-design" else ""

        class _ProviderStub:
            provider_name = "stub"
            model_name = "stub-model"
            base_url = ""
            thinking_enabled = False

            def __init__(self, events):
                self.events = events

            def chat_stream(self, messages, tools=None):
                self.events.append(("request", [msg.get("content", "") for msg in messages if msg.get("role") == "system"]))
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
                        "ppt_agent_mode": True,
                        "ppt_agent_strategy": PPT_AGENT_STRATEGY_HUASHU,
                        "ppt_agent_selected_strategy": PPT_AGENT_STRATEGY_HUASHU,
                        "selected_skill_names": ["huashu-design"],
                    },
                )
                worker.observability_signal.connect(lambda data: events.append(data))
                worker.run()

            request_system_text = "\n".join(provider_events[0][1])
            self.assertIn("Huashu Design Full Skill", request_system_text)
            append_events = [event for event in events if event.get("type") == "system_prompt_append"]
            self.assertEqual(append_events[0]["source"], "selected_skill_prompt")
            self.assertEqual(append_events[0]["skill_names"], ["huashu-design"])
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

            self.assertTrue(results)
            result = results[0]
            self.assertEqual(provider.calls, 1)
            self.assertIn("function.name", result["error"])
            self.assertIn("未提交不完整 assistant tool-call 消息", result["error"])
            self.assertFalse(any(msg.get("tool_calls") for msg in result["generated_messages"]))
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
                        "messages": messages,
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
                        "messages": messages,
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
            self.assertEqual(
                provider_calls[1]["messages"][:len(provider_calls[0]["messages"])],
                provider_calls[0]["messages"],
            )
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

    def test_legacy_clarifying_context_runs_as_execution(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self):
                names = [
                    "bash",
                    "apply_patch",
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
                    run_context={"mode": "clarifying"},
                )

            tool_names = {item["function"]["name"] for item in worker.tools}
            self.assertEqual(worker.run_context["mode"], RUN_MODE_EXECUTION)
            self.assertIn("search_codebase", tool_names)
            self.assertIn("workspace_list_files", tool_names)
            self.assertIn("text_file_read", tool_names)
            self.assertIn("glob", tool_names)
            self.assertIn("grep", tool_names)
            self.assertIn("request_user_input", tool_names)
            self.assertIn("bash", tool_names)
            self.assertIn("apply_patch", tool_names)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_llm_worker_accepts_legacy_planning_context_as_execution(self):
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

            self.assertEqual(worker.run_context["mode"], RUN_MODE_EXECUTION)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_llm_worker_does_not_limit_ordinary_clarification_rounds(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, run_mode=None, discovered_tool_names=None, include_deferred=False):
                names = ["request_user_input"]
                return [
                    {"type": "function", "function": {"name": name, "description": "", "parameters": {}}}
                    for name in names
                ]

            def is_tool_allowed(self, name, run_mode):
                return True

            def is_tool_visible(self, name, run_mode, discovered_tool_names=None):
                return True

            def get_skill_of_tool(self, _tool_name):
                return None

            def get_brief_skill_prompt(self, _skill_name):
                return ""

            def get_system_prompts(self, *args, **kwargs):
                return ""

            def call_tool(self, name, args, context=None):
                return {"source_tool": name, "content": "unexpected"}

            def check_for_updates(self):
                return False

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
                        "id": "input-1",
                        "function": {
                            "name": "request_user_input",
                            "arguments": "{\"message\":\"choose\",\"questions\":[{\"id\":\"scope\",\"question\":\"scope?\",\"options\":[{\"label\":\"推荐\",\"value\":\"recommended\"}]}]}",
                        },
                    }
                    return
                yield {"type": "content", "content": "continued"}

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        results = []
        try:
            with (
                patch("core.agent.SkillManager", _SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=_ProviderStub()),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "clarify this"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION, "clarify_round_count": 3},
                )
                worker.finished_signal.connect(lambda payload: results.append(payload))
                worker.run()

            self.assertTrue(results)
            generated = results[0]["generated_messages"]
            tool_messages = [msg for msg in generated if msg.get("role") == "tool"]
            self.assertTrue(tool_messages)
            self.assertIn("unexpected", tool_messages[0].get("content", ""))
            self.assertNotIn("limit reached", tool_messages[0].get("content", ""))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_grilling_allows_tenth_question_and_blocks_eleventh(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return [
                    {
                        "type": "function",
                        "function": {
                            "name": "request_user_input",
                            "description": "",
                            "parameters": {},
                        },
                    }
                ]

            def is_tool_allowed(self, name, run_mode):
                return True

            def is_tool_visible(self, name, run_mode, discovered_tool_names=None, **kwargs):
                return True

            def get_skill_of_tool(self, _tool_name):
                return None

            def get_brief_skill_prompt(self, _skill_name):
                return ""

            def get_system_prompts(self, *args, **kwargs):
                return ""

            def call_tool(self, name, args, context=None):
                return {
                    "source_tool": name,
                    "purpose": "",
                    "content": "accepted",
                    "answers": {"scope": {"selected_options": ["recommended"], "text": ""}},
                    "interaction_response": {"status": "completed", "approved": True},
                }

            def check_for_updates(self):
                return False

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
                        "id": "input-1",
                        "function": {
                            "name": "request_user_input",
                            "arguments": "{\"message\":\"choose\",\"questions\":[{\"id\":\"scope\",\"question\":\"scope?\",\"options\":[{\"label\":\"推荐\",\"value\":\"recommended\"}]}]}",
                        },
                    }
                    return
                yield {"type": "content", "content": "continued"}

        from core.agent import LLMWorker

        def run_with_round(round_count):
            provider = _ProviderStub()
            results = []
            with (
                patch("core.agent.SkillManager", _SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=provider),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "grill this"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_GRILLING, "grill_round_count": round_count},
                )
                worker.finished_signal.connect(results.append)
                worker.run()
            tool_messages = [
                msg for msg in results[0]["generated_messages"] if msg.get("role") == "tool"
            ]
            return worker, tool_messages[0].get("content", "")

        temp_dir = tempfile.mkdtemp()
        try:
            tenth_worker, tenth_result = run_with_round(GRILL_MAX_ROUNDS - 1)
            self.assertIn("accepted", tenth_result)
            self.assertEqual(tenth_worker.run_context["grill_round_count"], GRILL_MAX_ROUNDS)

            eleventh_worker, eleventh_result = run_with_round(GRILL_MAX_ROUNDS)
            self.assertIn("grilling round limit reached", eleventh_result)
            self.assertEqual(eleventh_worker.run_context["grill_round_count"], GRILL_MAX_ROUNDS)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_grill_checkpoint_execute_continue_and_custom_transitions(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, run_mode=None, *args, **kwargs):
                name = "apply_patch" if run_mode == RUN_MODE_EXECUTION else "text_file_read"
                return [
                    {
                        "type": "function",
                        "function": {"name": name, "description": "", "parameters": {}},
                    }
                ]

            def check_for_updates(self):
                return False

        from core.agent import LLMWorker

        args = {
            "purpose": GRILL_CHECKPOINT_PURPOSE,
            "questions": [
                {
                    "id": "grill_next_action",
                    "question": "下一步？",
                    "options": [
                        {"label": "确认并执行", "value": "execute"},
                        {"label": "继续拷问", "value": "continue"},
                    ],
                }
            ],
        }
        temp_dir = tempfile.mkdtemp()
        try:
            with patch("core.agent.SkillManager", _SkillManagerStub):
                execute_worker = LLMWorker(
                    [{"role": "user", "content": "grill this"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_GRILLING, "grill_round_count": 4},
                )
                execute_result = execute_worker._apply_grill_checkpoint_result(
                    args,
                    {
                        "answers": {
                            "grill_next_action": {
                                "selected_options": ["execute"],
                                "text": "",
                            }
                        }
                    },
                )
                self.assertEqual(execute_worker.run_context["mode"], RUN_MODE_EXECUTION)
                self.assertTrue(execute_worker.run_context["grill_execution_confirmed"])
                self.assertEqual(execute_result["mode_transition"]["to"], RUN_MODE_EXECUTION)
                self.assertIn(
                    "apply_patch",
                    {item["function"]["name"] for item in execute_worker.tools},
                )

                for selected, expected_choice in (("continue", "continue"), ("__custom__", "custom")):
                    continue_worker = LLMWorker(
                        [{"role": "user", "content": "grill this"}],
                        _ConfigStub(temp_dir),
                        workspace_dir=temp_dir,
                        run_context={
                            "mode": RUN_MODE_GRILLING,
                            "grill_round_count": GRILL_MAX_ROUNDS,
                            "grill_cycle_count": 0,
                        },
                    )
                    continued = continue_worker._apply_grill_checkpoint_result(
                        args,
                        {
                            "answers": {
                                "grill_next_action": {
                                    "selected_options": [selected],
                                    "text": "继续深入" if selected == "__custom__" else "",
                                }
                            }
                        },
                    )
                    self.assertEqual(continue_worker.run_context["mode"], RUN_MODE_GRILLING)
                    self.assertEqual(continue_worker.run_context["grill_round_count"], 0)
                    self.assertEqual(continue_worker.run_context["grill_cycle_count"], 1)
                    self.assertEqual(continued["grill_cycle_transition"]["choice"], expected_choice)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_grill_checkpoint_timeout_stops_without_another_model_request(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return [
                    {
                        "type": "function",
                        "function": {
                            "name": "request_user_input",
                            "description": "",
                            "parameters": {},
                        },
                    }
                ]

            def is_tool_allowed(self, *args, **kwargs):
                return True

            def is_tool_visible(self, *args, **kwargs):
                return True

            def get_skill_of_tool(self, _name):
                return None

            def get_system_prompts(self, *args, **kwargs):
                return ""

            def get_brief_skill_prompt(self, _name):
                return ""

            def call_tool(self, name, args, context=None):
                return {
                    "source_tool": name,
                    "purpose": GRILL_CHECKPOINT_PURPOSE,
                    "content": "未收到有效输入（timeout）。",
                    "answers": {},
                    "interaction_response": {"status": "timeout", "approved": False},
                }

            def check_for_updates(self):
                return False

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
                    yield {"type": "content", "content": "目标、风险和未决项总结。"}
                    return
                yield {
                    "type": "tool_call",
                    "index": 0,
                    "id": "checkpoint-1",
                    "function": {
                        "name": "request_user_input",
                        "arguments": "{\"message\":\"下一步\",\"purpose\":\"grill_checkpoint\",\"questions\":[{\"id\":\"grill_next_action\",\"question\":\"下一步？\",\"options\":[{\"label\":\"确认并执行\",\"value\":\"execute\"},{\"label\":\"继续拷问\",\"value\":\"continue\"}]}]}",
                    },
                }

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        provider = _ProviderStub()
        results = []
        try:
            with (
                patch("core.agent.SkillManager", _SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=provider),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "grill this"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_GRILLING, "grill_round_count": 2},
                )
                worker.finished_signal.connect(results.append)
                worker.run()

            self.assertEqual(provider.calls, 2)
            self.assertTrue(worker.run_context["grill_checkpoint_cancelled"])
            self.assertEqual(results[0]["content"], "本次拷问已停止，未执行原任务。")
            self.assertFalse(worker.run_context["grill_execution_confirmed"])
            self.assertTrue(
                any(
                    message.get("role") == "assistant"
                    and "目标、风险和未决项总结" in str(message.get("content") or "")
                    for message in results[0]["generated_messages"]
                )
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_grill_question_timeout_stops_without_another_model_request(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return [
                    {
                        "type": "function",
                        "function": {
                            "name": "request_user_input",
                            "description": "",
                            "parameters": {},
                        },
                    }
                ]

            def is_tool_allowed(self, *args, **kwargs):
                return True

            def is_tool_visible(self, *args, **kwargs):
                return True

            def get_skill_of_tool(self, _name):
                return None

            def get_system_prompts(self, *args, **kwargs):
                return ""

            def get_brief_skill_prompt(self, _name):
                return ""

            def call_tool(self, name, args, context=None):
                return {
                    "source_tool": name,
                    "purpose": "",
                    "content": "未收到有效输入（timeout）。",
                    "answers": {},
                    "interaction_response": {"status": "timeout", "approved": False},
                }

            def check_for_updates(self):
                return False

        class _ProviderStub:
            provider_name = "stub"
            model_name = "stub-model"
            base_url = ""
            thinking_enabled = False

            def __init__(self):
                self.calls = 0

            def chat_stream(self, messages, tools=None):
                self.calls += 1
                yield {
                    "type": "tool_call",
                    "index": 0,
                    "id": "question-1",
                    "function": {
                        "name": "request_user_input",
                        "arguments": "{\"message\":\"选择范围\",\"questions\":[{\"id\":\"scope\",\"question\":\"范围？\",\"options\":[{\"label\":\"核心范围\",\"value\":\"core\"}]}]}",
                    },
                }

        from core.agent import LLMWorker

        temp_dir = tempfile.mkdtemp()
        provider = _ProviderStub()
        results = []
        try:
            with (
                patch("core.agent.SkillManager", _SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=provider),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "grill this"}],
                    _ConfigStub(temp_dir),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_GRILLING},
                )
                worker.finished_signal.connect(results.append)
                worker.run()

            self.assertEqual(provider.calls, 1)
            self.assertTrue(worker.run_context["grill_input_cancelled"])
            self.assertEqual(results[0]["content"], "本次拷问已停止，未执行原任务。")
            self.assertFalse(worker.run_context["grill_execution_confirmed"])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

