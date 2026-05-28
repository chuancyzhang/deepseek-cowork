import unittest
import os
import sys
import tempfile
import shutil
import threading
import time
import json
import subprocess
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_manager import ConfigManager
from core.skill_manager import SkillManager
from core.interaction import InteractionBridge, interaction_service, parse_interaction_reply
from core import env_utils
from core import sandbox_runtime
from core.clarify_mode import RUN_MODE_EXECUTION
from core.agent import LLMWorker
from core.daemon import DaemonClient, DaemonRequestHandler, DaemonServer, DaemonState
from core.single_instance import (
    UiSingleInstanceServer,
    build_ui_server_name,
    notify_existing_ui,
)
from core.chat_storage import ChatStorage
from core.im_session_key import build_im_session_key, parse_im_session_key, resolve_date_key
from core.llm.deepseek import (
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_DEEPSEEK_REASONING_EFFORT,
    DEFAULT_DEEPSEEK_THINKING_ENABLED,
)

class TestConfigManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = os.path.join(self.temp_dir, "config.json")
        # Mock sys.executable to control config path logic if needed, 
        # but ConfigManager logic is complex regarding paths.
        # For simplicity, we just test basic dict operations if we can bypass load.
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _create_config_manager(self, payload=None):
        if payload is not None:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        with patch("core.config_manager.get_app_data_dir", return_value=self.temp_dir), \
             patch("core.config_manager.get_base_dir", return_value=self.temp_dir):
            return ConfigManager()

    def test_set_get_config(self):
        # We need to patch where ConfigManager looks for files or just test the dict logic
        cm = self._create_config_manager()
        cm.config = {} # Reset
        cm.set("api_key", "sk-test")
        self.assertEqual(cm.get("api_key"), "sk-test")

    def test_defaults_include_new_deepseek_settings(self):
        cm = self._create_config_manager()
        self.assertEqual(cm.get("model_name"), DEFAULT_DEEPSEEK_MODEL)
        self.assertEqual(cm.get("deepseek_reasoning_effort"), DEFAULT_DEEPSEEK_REASONING_EFFORT)
        self.assertEqual(cm.get("deepseek_thinking_enabled"), DEFAULT_DEEPSEEK_THINKING_ENABLED)
        self.assertEqual(cm.get("deepseek_v4_context_window_tokens"), 1000000)
        self.assertEqual(cm.get("context_budget_ratio"), 0.8)
        self.assertEqual(cm.get("context_compression_recent_keep_turns"), 40)
        self.assertTrue(cm.get("model_channels"))
        self.assertTrue(cm.get("model_provider_configs"))
        self.assertEqual(cm.get_selected_model_id(), "openai-default")

    def test_project_config_adds_renames_pins_and_hides(self):
        cm = self._create_config_manager()
        project_dir = os.path.join(self.temp_dir, "demo")
        os.makedirs(project_dir)

        project = cm.upsert_project(project_dir, name="Demo", pinned=True)
        self.assertEqual(project["name"], "Demo")
        self.assertTrue(project["pinned"])
        self.assertEqual(len(cm.get_projects()), 1)

        cm.upsert_project(project_dir, name="Renamed", pinned=False)
        projects = cm.get_projects()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["name"], "Renamed")
        self.assertFalse(projects[0]["pinned"])

        self.assertTrue(cm.hide_project(project_dir))
        self.assertEqual(cm.get_projects(include_hidden=False), [])
        self.assertTrue(cm.restore_project(project_dir))
        self.assertEqual(len(cm.get_projects(include_hidden=False)), 1)

    def test_migrates_legacy_model_config_to_provider_group(self):
        cm = self._create_config_manager(
            {
                "llm_provider": "openai",
                "api_key": "legacy-key",
                "base_url": "https://legacy.example",
                "model_name": "legacy-model",
                "deepseek_thinking_enabled": False,
                "deepseek_reasoning_effort": "max",
            }
        )

        profile = cm.get_model_profile()

        self.assertEqual(profile["provider"], "openai")
        self.assertEqual(profile["api_key"], "legacy-key")
        self.assertEqual(profile["base_url"], "https://legacy.example")
        self.assertEqual(profile["model_name"], "legacy-model")
        self.assertFalse(profile["deepseek_thinking_enabled"])
        self.assertEqual(profile["deepseek_reasoning_effort"], "max")
        self.assertEqual(cm.get("llm_provider"), "openai")
        self.assertEqual(cm.get("model_name"), "legacy-model")
        self.assertEqual(profile["channel_id"], "openai-default-channel")
        self.assertEqual(profile["channel_display_name"], "OpenAI 兼容服务")

    def test_migrates_provider_configs_to_model_channels(self):
        cm = self._create_config_manager(
            {
                "selected_model_id": "openai-custom",
                "model_provider_configs": {
                    "openai": {
                        "display_name": "Tencent OpenAI",
                        "api_key": "tencent-key",
                        "base_url": "https://tencent.example/v1",
                        "models": [
                            {
                                "id": "openai-custom",
                                "display_name": "GLM Test",
                                "model_name": "glm-test",
                            }
                        ],
                    }
                },
            }
        )

        channels = cm.get_model_channels()
        profile = cm.get_model_profile("openai-custom")

        self.assertTrue(any(channel["channel_id"] == "openai-default-channel" for channel in channels))
        self.assertEqual(profile["channel_display_name"], "Tencent OpenAI")
        self.assertEqual(profile["base_url"], "https://tencent.example/v1")
        self.assertEqual(profile["model_name"], "glm-test")

    def test_set_selected_model_id_syncs_legacy_fields(self):
        cm = self._create_config_manager()
        configs = cm.get_model_provider_configs()
        configs["anthropic"]["api_key"] = "anthropic-key"
        configs["anthropic"]["base_url"] = "https://anthropic.example"
        configs["anthropic"]["models"] = [
            {
                "id": "anthropic-custom",
                "display_name": "Claude Test",
                "model_name": "claude-test",
            }
        ]
        cm.set_model_provider_configs(configs, "anthropic-custom")

        self.assertEqual(cm.get_selected_model_id(), "anthropic-custom")
        self.assertEqual(cm.get("llm_provider"), "anthropic")
        self.assertEqual(cm.get("api_key"), "anthropic-key")
        self.assertEqual(cm.get("base_url"), "https://anthropic.example")
        self.assertEqual(cm.get("model_name"), "claude-test")

    def test_model_channels_support_multiple_openai_base_urls(self):
        cm = self._create_config_manager()
        channels = [
            {
                "channel_id": "deepseek-channel",
                "display_name": "DeepSeek",
                "provider_type": "openai",
                "api_key": "deepseek-key",
                "base_url": "https://api.deepseek.com",
                "models": [
                    {
                        "id": "shared-model",
                        "display_name": "DeepSeek Chat",
                        "model_name": "deepseek-chat",
                        "deepseek_thinking_enabled": True,
                        "deepseek_reasoning_effort": "high",
                    }
                ],
            },
            {
                "channel_id": "tencent-channel",
                "display_name": "腾讯云",
                "provider_type": "openai",
                "api_key": "tencent-key",
                "base_url": "https://tencent.example/v1",
                "models": [
                    {
                        "id": "shared-model",
                        "display_name": "GLM",
                        "model_name": "glm-test",
                    }
                ],
            },
        ]

        cm.set_model_channels(channels, "shared-model-2")
        profiles = cm.iter_model_profiles()
        ids = [profile["id"] for profile in profiles]
        selected = cm.get_model_profile()

        self.assertEqual(ids, ["shared-model", "shared-model-2"])
        self.assertEqual(selected["channel_id"], "tencent-channel")
        self.assertEqual(selected["api_key"], "tencent-key")
        self.assertEqual(selected["base_url"], "https://tencent.example/v1")
        self.assertEqual(cm.get("llm_provider"), "openai")
        self.assertEqual(cm.get("model_name"), "glm-test")

    def test_agent_profiles_are_normalized_and_persisted(self):
        cm = self._create_config_manager(
            {
                "agent_profiles": [
                    {
                        "name": "审查助手",
                        "description": "代码审查",
                        "system_prompt": "只做审查",
                        "skill_names": ["browser-automation", "browser-automation", ""],
                        "enabled": True,
                    },
                    {
                        "name": "审查助手",
                        "skill_names": ["command-tools"],
                    },
                    {
                        "name": "",
                        "skill_names": ["ignored"],
                    },
                ]
            }
        )

        profiles = cm.get_agent_profiles()

        self.assertEqual(len(profiles), 2)
        self.assertEqual(profiles[0]["name"], "审查助手")
        self.assertEqual(profiles[0]["skill_names"], ["browser-automation"])
        self.assertNotEqual(profiles[0]["id"], profiles[1]["id"])

        cm.set_agent_profiles(
            [
                {
                    "id": "agent-writer",
                    "name": "写作助手",
                    "description": "输出润色",
                    "system_prompt": "更简洁",
                    "skill_names": ["command-tools"],
                    "enabled": False,
                }
            ]
        )
        stored = cm.get_agent_profile("agent-writer")
        self.assertIsNotNone(stored)
        self.assertEqual(stored["name"], "写作助手")
        self.assertFalse(stored["enabled"])

    def test_sop_templates_are_normalized_and_persisted(self):
        cm = self._create_config_manager(
            {
                "sop_templates": [
                    {
                        "name": "办公流程",
                        "description": "示例",
                        "skill_names": ["browser-automation", "browser-automation", ""],
                        "triggers": ["办公", "办公", ""],
                        "steps": [
                            {
                                "title": "确认目标",
                                "instructions": "只确认目标",
                                "success_criteria": "目标清楚",
                                "allow_skip": False,
                            },
                            {
                                "title": "",
                                "instructions": "",
                                "success_criteria": "",
                            },
                        ],
                    },
                    {
                        "name": "办公流程",
                        "steps": [{"title": "第二步"}],
                    },
                ]
            }
        )

        templates = cm.get_sop_templates()

        self.assertEqual(len(templates), 2)
        self.assertEqual(templates[0]["name"], "办公流程")
        self.assertEqual(templates[0]["skill_names"], ["browser-automation"])
        self.assertEqual(templates[0]["triggers"], ["办公"])
        self.assertEqual(len(templates[0]["steps"]), 1)
        self.assertNotEqual(templates[0]["id"], templates[1]["id"])

        cm.set_sop_templates(
            [
                {
                    "id": "office-flow",
                    "name": "Office Flow",
                    "description": "demo",
                    "default_agent_profile_id": "agent-review",
                    "steps": [{"title": "Step 1", "instructions": "Do it"}],
                }
            ]
        )
        stored = cm.get_sop_template("office-flow")
        self.assertIsNotNone(stored)
        self.assertEqual(stored["name"], "Office Flow")
        self.assertEqual(stored["default_agent_profile_id"], "agent-review")

    def test_migrates_legacy_deepseek_model_name(self):
        cm = self._create_config_manager({"model_name": "deepseek-reasoner"})
        self.assertEqual(cm.get("model_name"), DEFAULT_DEEPSEEK_MODEL)
        with open(self.config_file, "r", encoding="utf-8") as f:
            stored = json.load(f)
        self.assertEqual(stored["model_name"], DEFAULT_DEEPSEEK_MODEL)

    def test_preserves_custom_model_name(self):
        cm = self._create_config_manager({"model_name": "deepseek-r1-custom"})
        self.assertEqual(cm.get("model_name"), "deepseek-r1-custom")

class TestSkillManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.temp_dir, "skills")
        os.makedirs(self.skills_dir)
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_load_skills(self):
        # Create a dummy skill
        skill_name = "test-skill"
        skill_path = os.path.join(self.skills_dir, skill_name)
        os.makedirs(skill_path)
        
        with open(os.path.join(skill_path, "SKILL.md"), "w") as f:
            f.write("---\nname: test-skill\n---\nTest skill description.")
            
        with open(os.path.join(skill_path, "impl.py"), "w") as f:
            f.write("def test_func():\n    return 'hello'")
            
        # Patch the skills_dirs detection
        with patch.object(SkillManager, '__init__', return_value=None) as mock_init:
            sm = SkillManager()
            sm.skills_dirs = [self.skills_dir]
            sm.tools = {}
            sm.tool_definitions = []
            sm.skill_prompts = []
            sm.config_manager = None
            
            # Call load_skills directly
            SkillManager.load_skills(sm)
            
            self.assertIn("test_func", sm.tools)
            self.assertEqual(sm.tools["test_func"](), "hello")

class TestInteractionBridge(unittest.TestCase):
    def test_bridge_singleton(self):
        from core.interaction import bridge
        self.assertIsInstance(bridge, InteractionBridge)

class TestEnvUtils(unittest.TestCase):
    def tearDown(self):
        sandbox_runtime._RUNTIME_CACHE = None

    def test_get_python_executable_returns_empty_when_unavailable(self):
        with patch.object(env_utils.sys, "frozen", True, create=True), \
             patch.object(env_utils.sys, "executable", r"C:\app\deepseek-cowork.exe"), \
             patch.object(env_utils.sys, "exec_prefix", r"C:\app"), \
             patch.object(env_utils.sys, "base_prefix", r"C:\app", create=True), \
             patch("core.env_utils.os.path.isfile", return_value=False), \
             patch("core.env_utils.shutil.which", return_value=None), \
             patch("core.env_utils.os.getenv", return_value=""):
            self.assertEqual(env_utils.get_python_executable(), "")

    def test_get_python_executable_frozen_does_not_fallback_to_system_python(self):
        with patch.object(env_utils.sys, "frozen", True, create=True), \
             patch.object(env_utils.sys, "executable", r"C:\app\deepseek-cowork.exe"), \
             patch.object(env_utils.sys, "exec_prefix", r"C:\Python311"), \
             patch.object(env_utils.sys, "base_prefix", r"C:\Python311", create=True), \
             patch("core.env_utils.os.path.isfile", return_value=False), \
             patch("core.env_utils.shutil.which", return_value=r"C:\Python311\python.exe"), \
             patch("core.env_utils.os.getenv", return_value=""):
            self.assertEqual(env_utils.get_python_executable(), "")

    def test_runtime_snapshot_resolves_bundled_python_node_and_bash(self):
        temp_dir = tempfile.mkdtemp()
        try:
            python_dir = os.path.join(temp_dir, "python_env")
            node_dir = os.path.join(temp_dir, "node_env")
            bash_dir = os.path.join(temp_dir, "_internal", "git_bash_env", "bin")
            os.makedirs(python_dir, exist_ok=True)
            os.makedirs(node_dir, exist_ok=True)
            os.makedirs(bash_dir, exist_ok=True)
            python_exe = os.path.join(python_dir, "python.exe")
            node_exe = os.path.join(node_dir, "node.exe")
            bash_exe = os.path.join(bash_dir, "bash.exe")
            for path in (python_exe, node_exe, bash_exe):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")
            sandbox_runtime._RUNTIME_CACHE = None
            with patch("core.sandbox_runtime.get_base_dir", return_value=temp_dir), \
                 patch("core.sandbox_runtime.get_app_data_dir", return_value=os.path.join(temp_dir, "data")), \
                 patch("core.sandbox_runtime._copy_runtime_dir", side_effect=lambda source, _name: source), \
                 patch.object(sandbox_runtime.sys, "frozen", True, create=True), \
                 patch.object(env_utils.sys, "frozen", True, create=True):
                snapshot = env_utils.get_runtime_snapshot()
            self.assertEqual(snapshot["python"]["path"], python_exe)
            self.assertEqual(snapshot["node"]["path"], node_exe)
            self.assertEqual(snapshot["bash"]["path"], bash_exe)
            self.assertTrue(snapshot["python"]["available"])
            self.assertTrue(snapshot["node"]["available"])
            self.assertTrue(snapshot["bash"]["available"])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_sandbox_env_adds_skill_dependency_paths(self):
        temp_dir = tempfile.mkdtemp()
        try:
            sandbox_runtime._RUNTIME_CACHE = None
            with patch("core.sandbox_runtime.get_base_dir", return_value=temp_dir), \
                 patch("core.sandbox_runtime.get_app_data_dir", return_value=os.path.join(temp_dir, "data")):
                env = sandbox_runtime.build_sandbox_env(workspace_dir=temp_dir, skill_id="demo-skill")
            self.assertIn(os.path.join("demo-skill", "python", "site-packages"), env["PYTHONPATH"])
            self.assertIn(os.path.join("demo-skill", "node", "node_modules"), env["NODE_PATH"])
            self.assertEqual(env["COWORK_WORKSPACE_DIR"], os.path.abspath(temp_dir))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_ensure_package_installed_reports_missing_runtime(self):
        env_utils._INSTALL_FAILED.clear()
        with patch.object(env_utils, "get_python_executable", return_value=""), \
             patch.object(env_utils.importlib, "import_module", side_effect=ImportError()):
            with self.assertRaises(RuntimeError) as cm:
                env_utils.ensure_package_installed("openpyxl")
            self.assertIn("bundled Python runtime is missing", str(cm.exception))

class _DaemonConfigStub:
    def __init__(self, history_dir, values=None, profile=None):
        self._history_dir = history_dir
        self._values = dict(values or {})
        self._profile = profile
    def get_chat_history_dir(self):
        os.makedirs(self._history_dir, exist_ok=True)
        return self._history_dir
    def get(self, key, default=None):
        return self._values.get(key, default)
    def get_model_profile(self, model_id=None):
        return self._profile


class _PromptSkillManagerStub:
    def get_tool_definitions(self, *args, **kwargs):
        return []
    def get_system_prompts(self, *args, **kwargs):
        return ""
    def get_brief_skill_prompt(self, skill_name):
        return ""
    def get_skill_display_name(self, skill_name):
        return skill_name

class TestInteractionService(unittest.TestCase):
    def setUp(self):
        self.service = InteractionBridge()

    def _wait_for_pending(self, session_id, timeout=1.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            pending = self.service.get_pending_request(session_id)
            if pending:
                return pending
            time.sleep(0.01)
        self.fail(f"Pending interaction for {session_id} was not created in time.")

    def test_request_resolve_roundtrip(self):
        result_holder = {}

        def worker():
            result_holder["result"] = self.service.create_request(
                "session-a",
                "approval",
                "continue?",
                timeout_seconds=1,
            )

        thread = threading.Thread(target=worker)
        thread.start()
        pending = self._wait_for_pending("session-a")
        self.assertTrue(self.service.resolve_request(pending["request_id"], True))
        thread.join(1)

        result = result_holder["result"]
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["approved"])

    def test_sessions_are_isolated(self):
        results = {}

        def worker(session_id):
            results[session_id] = self.service.create_request(
                session_id,
                "text",
                f"input for {session_id}",
                timeout_seconds=1,
            )

        thread_a = threading.Thread(target=worker, args=("session-a",))
        thread_b = threading.Thread(target=worker, args=("session-b",))
        thread_a.start()
        thread_b.start()

        pending_a = self._wait_for_pending("session-a")
        pending_b = self._wait_for_pending("session-b")
        self.assertEqual(pending_a["session_id"], "session-a")
        self.assertEqual(pending_b["session_id"], "session-b")
        self.assertNotEqual(pending_a["request_id"], pending_b["request_id"])

        self.service.resolve_request(pending_a["request_id"], "alpha")
        self.service.resolve_request(pending_b["request_id"], "beta")
        thread_a.join(1)
        thread_b.join(1)

        self.assertEqual(results["session-a"]["text"], "alpha")
        self.assertEqual(results["session-b"]["text"], "beta")

    def test_timeout_returns_timeout_payload(self):
        result = self.service.create_request(
            "session-timeout",
            "approval",
            "continue?",
            timeout_seconds=0.05,
        )
        self.assertEqual(result["status"], "timeout")
        self.assertFalse(result["approved"])

    def test_cancel_session_requests_unblocks_waiter(self):
        result_holder = {}

        def worker():
            result_holder["result"] = self.service.create_request(
                "session-cancel",
                "choice",
                "pick one",
                options=[{"label": "Alpha", "value": "alpha"}],
                timeout_seconds=1,
            )

        thread = threading.Thread(target=worker)
        thread.start()
        self._wait_for_pending("session-cancel")
        self.assertEqual(self.service.cancel_session_requests("session-cancel"), 1)
        thread.join(1)

        result = result_holder["result"]
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["approved"])

    def test_invalid_request_id_is_rejected(self):
        self.assertFalse(self.service.resolve_request("missing-request", True))

    def test_parse_interaction_reply_rejects_invalid_choice(self):
        payload, valid, error = parse_interaction_reply(
            {
                "request_id": "req-choice",
                "kind": "choice",
                "options": [{"label": "Alpha", "value": "alpha"}],
                "allow_free_text": False,
            },
            "unknown",
        )
        self.assertFalse(valid)
        self.assertEqual(payload["status"], "invalid")
        self.assertIn("choice", error)


class TestDaemonState(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state = DaemonState(_DaemonConfigStub(self.temp_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_is_context_overflow_error(self):
        self.assertTrue(self.state._is_context_overflow_error({"error": "maximum context length exceeded"}))
        self.assertFalse(self.state._is_context_overflow_error({"error": "network timeout"}))

    def test_deepseek_v4_uses_large_context_budget(self):
        state = DaemonState(
            _DaemonConfigStub(
                self.temp_dir,
                values={"context_budget_ratio": 0.8},
                profile={"model_name": "deepseek-v4-pro"},
            )
        )

        self.assertEqual(state._context_window_tokens({"selected_model_id": "openai-default"}), 1000000)
        self.assertEqual(state._context_budget_threshold({"selected_model_id": "openai-default"}), 800000)

    def test_deepseek_v4_does_not_compress_under_budget(self):
        state = DaemonState(
            _DaemonConfigStub(
                self.temp_dir,
                values={"context_budget_ratio": 0.8},
                profile={"model_name": "deepseek-v4-pro"},
            )
        )
        session_id = "im-v4-under-budget"
        im_key = build_im_session_key("u1", "chat-a", "2026-05-25")
        state.chat_storage.upsert_im_session("feishu", im_key, session_id)
        messages = [{"role": "user", "content": "x" * 800000}]

        self.assertIsNone(state._build_overflow_retry_messages(session_id, messages, run_context={}, force=False))

    def test_budget_compression_keeps_recent_tail_and_summary(self):
        state = DaemonState(
            _DaemonConfigStub(
                self.temp_dir,
                values={
                    "context_window_tokens": 1000,
                    "context_budget_ratio": 0.5,
                    "context_compression_recent_keep_turns": 2,
                },
                profile={"model_name": "small-model"},
            )
        )
        session_id = "im-budget-compress"
        im_key = build_im_session_key("u1", "chat-b", "2026-05-25")
        state.chat_storage.upsert_im_session("feishu", im_key, session_id)
        messages = [
            {"role": "user", "content": "目标 " + ("x" * 1000)},
            {"role": "assistant", "content": "决定采用方案 A"},
            {"role": "user", "content": "继续 " + ("y" * 1000)},
            {"role": "assistant", "content": "完成处理"},
        ]

        compressed = state._build_overflow_retry_messages(session_id, messages, run_context={}, force=False)

        self.assertIsNotNone(compressed)
        self.assertEqual(compressed[0]["role"], "system")
        self.assertIn("Context Summary", compressed[0]["content"])
        self.assertEqual(compressed[-2:], messages[-2:])

    def test_compression_boundary_keeps_tool_round_together(self):
        state = DaemonState(
            _DaemonConfigStub(
                self.temp_dir,
                values={
                    "context_window_tokens": 1000,
                    "context_budget_ratio": 0.5,
                    "context_compression_recent_keep_turns": 2,
                },
                profile={"model_name": "small-model"},
            )
        )
        session_id = "im-tool-boundary"
        im_key = build_im_session_key("u1", "chat-c", "2026-05-25")
        state.chat_storage.upsert_im_session("feishu", im_key, session_id)
        tool_assistant = {
            "role": "assistant",
            "content": "",
            "reasoning_content": "need tool",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "demo", "arguments": "{}"},
                }
            ],
        }
        tool_result = {"role": "tool", "tool_call_id": "call-1", "content": "ok"}
        messages = [
            {"role": "user", "content": "old " + ("x" * 2500)},
            {"role": "assistant", "content": "old answer"},
            tool_assistant,
            tool_result,
            {"role": "assistant", "content": "done"},
        ]

        compressed = state._build_overflow_retry_messages(session_id, messages, run_context={}, force=False)

        self.assertEqual(compressed[1], tool_assistant)
        self.assertEqual(compressed[2], tool_result)


class TestAgentSystemPrompt(unittest.TestCase):
    def test_dynamic_status_is_after_stable_policy(self):
        temp_dir = tempfile.mkdtemp()
        try:
            worker = LLMWorker.__new__(LLMWorker)
            worker.workspace_dir = temp_dir
            worker.run_context = {"mode": RUN_MODE_EXECUTION}
            worker.tools = []
            worker.parent_agent_id = ""
            worker.skill_manager = _PromptSkillManagerStub()
            worker.config_manager = _DaemonConfigStub(temp_dir)
            prompt = worker._build_system_prompt(
                current_messages=[],
                runtime_snapshot={"version": "3.test", "python_exe": "python.exe"},
                sandbox_snapshot={
                    "python": {"available": True, "version": "3.test", "path": "python.exe"},
                    "node": {"available": False},
                    "bash": {"available": False},
                },
            )

            self.assertLess(prompt.index("策略 [技能创建]:"), prompt.index("# 当前运行状态"))
            self.assertLess(prompt.index("策略 [元工具导航]:"), prompt.index("当前运行模式: execution"))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_image_turn_hides_tool_search_without_prompt_changes(self):
        worker = LLMWorker.__new__(LLMWorker)
        worker.tools = [
            {"type": "function", "function": {"name": "tool_search"}},
            {"type": "function", "function": {"name": "read_file"}},
        ]
        tools = worker._tools_for_messages(
            [
                {
                    "role": "user",
                    "content": "你能看到这张图吗",
                    "content_parts": [{"type": "input_image", "path": "demo.png"}],
                }
            ]
        )

        tool_names = [item["function"]["name"] for item in tools]
        self.assertEqual(tool_names, ["read_file"])


class TestSingleInstance(unittest.TestCase):
    def test_build_ui_server_name_is_stable_and_scoped(self):
        first = build_ui_server_name(os.path.join("C:\\Apps", "Cowork"))
        second = build_ui_server_name(os.path.join("C:\\Apps", "Cowork"))
        other = build_ui_server_name(os.path.join("C:\\Apps", "Cowork2"))

        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith("deepseek-cowork-ui-"))

    def test_notify_existing_ui_triggers_activate(self):
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance() or QCoreApplication([])
        temp_dir = tempfile.mkdtemp()
        server_name = build_ui_server_name(temp_dir)
        activated = []
        server = UiSingleInstanceServer(server_name, lambda: activated.append(True))
        try:
            self.assertTrue(server.start())
            client_path = os.path.join(os.path.dirname(__file__), "single_instance_client.py")
            proc = subprocess.Popen(
                [sys.executable, client_path, server_name],
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.time() + 1
            while time.time() < deadline and (not activated or proc.poll() is None):
                app.processEvents()
                time.sleep(0.01)
            stdout, stderr = proc.communicate(timeout=1)
            self.assertEqual(proc.returncode, 0, stdout + stderr)
            self.assertEqual(activated, [True])
        finally:
            server.stop()
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestDaemonInteractionRoundtrip(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state = DaemonState(_DaemonConfigStub(self.temp_dir))
        self.server = DaemonServer(("127.0.0.1", 0), DaemonRequestHandler, self.state)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.client = DaemonClient(host=host, port=port, timeout=1)

    def tearDown(self):
        interaction_service.cancel_session_requests("daemon-session-test", reason="cancelled")
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_pending_interaction_and_respond_roundtrip(self):
        result_holder = {}

        def worker():
            result_holder["result"] = interaction_service.create_request(
                "daemon-session-test",
                "approval",
                "continue?",
                timeout_seconds=1,
            )

        thread = threading.Thread(target=worker)
        thread.start()
        pending = None
        deadline = time.time() + 1
        while time.time() < deadline:
            response = self.client.get_pending_interaction("daemon-session-test")
            pending = (response or {}).get("pending") if isinstance(response, dict) else None
            if pending:
                break
            time.sleep(0.01)
        self.assertIsNotNone(pending)

        ack = self.client.respond_interaction(pending["request_id"], True)
        thread.join(1)

        self.assertEqual(ack["status"], "ok")
        self.assertTrue(ack["resolved"])
        self.assertTrue(result_holder["result"]["approved"])
        self.assertEqual(result_holder["result"]["status"], "completed")

class TestImSessionKey(unittest.TestCase):
    def test_build_and_parse_im_session_key(self):
        key = build_im_session_key("u1", "c1", "2026-03-06")
        parsed = parse_im_session_key(key)
        self.assertEqual(parsed["im_user_id"], "u1")
        self.assertEqual(parsed["chat_id"], "c1")
        self.assertEqual(parsed["summary_date"], "2026-03-06")
    def test_resolve_date_key_from_millis_timestamp(self):
        date_key = resolve_date_key("1710000000000")
        self.assertRegex(date_key, r"^\d{4}-\d{2}-\d{2}$")

class TestImDailySummaryStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "chat_history.sqlite")
        self.storage = ChatStorage(self.db_path)
    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    def test_upsert_and_get_im_daily_summary(self):
        self.storage.upsert_im_daily_summary(
            "feishu",
            "user-a",
            "chat-a",
            "2026-03-06",
            "conv-1",
            "summary-a",
            12,
            token_estimate=30,
        )
        row = self.storage.get_im_daily_summary("feishu", "user-a", "chat-a", "2026-03-06")
        self.assertIsNotNone(row)
        self.assertEqual(row["summary_text"], "summary-a")
        self.assertEqual(row["source_message_upto_pos"], 12)
        self.storage.upsert_im_daily_summary(
            "feishu",
            "user-a",
            "chat-a",
            "2026-03-06",
            "conv-1",
            "summary-b",
            20,
            token_estimate=42,
        )
        row2 = self.storage.get_im_daily_summary("feishu", "user-a", "chat-a", "2026-03-06")
        self.assertEqual(row2["summary_text"], "summary-b")
        self.assertEqual(row2["source_message_upto_pos"], 20)

if __name__ == "__main__":
    unittest.main()
