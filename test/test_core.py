import unittest
import base64
import os
import sys
import tempfile
import shutil
import threading
import time
import json
import subprocess
import types
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_manager import ConfigManager, normalize_mcp_servers, parse_mcp_servers_json
from core.skill_manager import SkillManager
from core.interaction import InteractionBridge, interaction_service, parse_interaction_reply
from core import env_utils
from core import sandbox_runtime
from core.clarify_mode import RUN_MODE_EXECUTION
from core.agent import LLMWorker
from core import daemon as daemon_module
from core.daemon import DaemonClient, DaemonRequestHandler, DaemonServer, DaemonState
from core.mcp_client import (
    McpOperationError,
    _open_mcp_session,
    _open_streamable_http_transport,
    clear_mcp_auth_cache,
    describe_mcp_import_error,
    describe_mcp_operation_error,
    prepare_mcp_server_config,
)
from core.single_instance import (
    UiSingleInstanceServer,
    build_ui_server_name,
    notify_existing_ui,
    notify_existing_ui_with_retries,
)
from core.chat_storage import ChatStorage
from core.im_session_key import build_im_session_key, parse_im_session_key, resolve_date_key
from core.llm.deepseek import (
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_DEEPSEEK_REASONING_EFFORT,
    DEFAULT_DEEPSEEK_THINKING_ENABLED,
    DEEPSEEK_RESPONSES_REPLAY_INPUT_KEY,
    DEEPSEEK_RESPONSES_REPLAY_META_KEY,
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

    def test_batch_save_coalesces_multiple_writes(self):
        cm = self._create_config_manager()

        with patch.object(cm, "_write_config") as write_mock:
            with cm.batch_save():
                cm.set("api_key", "sk-test")
                cm.set("base_url", "https://example.com/v1")
                cm.set_chat_history_dir(os.path.join(self.temp_dir, "history"))
                cm.set_chat_workspace_root(os.path.join(self.temp_dir, "chat-workspaces"))
                cm.set_god_mode(True)

        self.assertEqual(write_mock.call_count, 1)

    def test_setters_skip_writes_when_values_do_not_change(self):
        cm = self._create_config_manager()

        with patch.object(cm, "_write_config") as write_mock:
            cm.set("api_key", cm.get("api_key"))
            cm.set_chat_history_dir(cm.get_chat_history_dir())
            cm.set_chat_workspace_root(cm.get_chat_workspace_root())
            cm.set_god_mode(cm.get_god_mode())
            cm.set_agent_profiles(cm.get_agent_profiles())
            cm.set_mcp_servers(cm.get_mcp_servers())

        self.assertEqual(write_mock.call_count, 0)

    def test_skill_config_normalizes_and_persists_values(self):
        cm = self._create_config_manager()

        cm.set_skill_config("feishu-docs", {" app_id ": "cli_a", "app_secret": None, "": "ignored"})

        self.assertEqual(
            cm.get_skill_config("feishu-docs"),
            {"app_id": "cli_a", "app_secret": ""},
        )
        self.assertEqual(cm.get_skill_config("missing"), {})

    def test_skill_config_empty_values_remove_skill_entry(self):
        cm = self._create_config_manager()
        cm.set_skill_config("dingtalk-docs", {"app_key": "key"})

        cm.set_skill_config("dingtalk-docs", {})

        self.assertEqual(cm.get_skill_config("dingtalk-docs"), {})

    def test_superset_skill_config_change_clears_managed_auth_cache(self):
        cm = self._create_config_manager({})
        with patch("core.config_manager.clear_mcp_auth_cache") as clear_cache:
            cm.set_skill_config("superset-mcp", {"SUPERSET_USERNAME": "admin"})
        clear_cache.assert_called_once_with()

    def test_managed_mcp_ownership_is_inferred_and_follows_skill_toggle(self):
        cm = self._create_config_manager({})
        cm.set_mcp_servers(
            [
                {
                    "id": "superset-mcp",
                    "name": "Superset MCP",
                    "enabled": True,
                    "transport": "streamable_http",
                    "url": "https://superset.example/mcp",
                    "auth": {"type": "superset_password", "skill_name": "superset-mcp"},
                },
                {
                    "id": "manual",
                    "name": "Manual MCP",
                    "enabled": True,
                    "transport": "streamable_http",
                    "url": "https://manual.example/mcp",
                },
            ]
        )

        stored = cm.get_mcp_servers()
        self.assertEqual(stored[0]["source_skill"], "superset-mcp")
        self.assertTrue(stored[0]["managed_by_skill"])
        self.assertFalse(stored[1]["managed_by_skill"])

        cm.set_skill_enabled("superset-mcp", False)
        stored = cm.get_mcp_servers()
        self.assertFalse(stored[0]["enabled"])
        self.assertTrue(stored[1]["enabled"])

        cm.set_skill_enabled("superset-mcp", True)
        self.assertTrue(cm.get_mcp_servers()[0]["enabled"])

        legacy_showdoc = normalize_mcp_servers(
            [
                {
                    "id": "showdoc",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "mcp-showdoc"],
                }
            ]
        )[0]
        self.assertEqual(legacy_showdoc["source_skill"], "showdoc-mcp")
        self.assertTrue(legacy_showdoc["managed_by_skill"])

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
        self.assertEqual(
            cm.get_chat_workspace_root(),
            os.path.join(self.temp_dir, "conversation_workspaces"),
        )

    def test_openai_models_default_to_chat_completions_without_saved_protocol(self):
        cm = self._create_config_manager()

        entry = cm._normalize_model_entry(
            "openai",
            {"model_name": "gpt-5.6", "display_name": "Existing GPT-5.6"},
        )

        self.assertEqual(entry["api_protocol"], "chat_completions")

    def test_openai_model_preserves_responses_protocol(self):
        cm = self._create_config_manager()

        entry = cm._normalize_model_entry(
            "openai",
            {
                "model_name": "gpt-5.6",
                "display_name": "GPT-5.6",
                "api_protocol": "responses",
            },
        )

        self.assertEqual(entry["api_protocol"], "responses")

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
        archived = cm.get_projects(include_hidden=True)[0]
        self.assertTrue(archived["archived"])
        self.assertGreater(archived["archived_at"], 0)
        self.assertEqual(cm.get_projects(include_hidden=False), [])
        self.assertTrue(cm.restore_project(project_dir))
        restored = cm.get_projects(include_hidden=False)
        self.assertEqual(len(restored), 1)
        self.assertFalse(restored[0]["archived"])
        self.assertEqual(restored[0]["archived_at"], 0)

    def test_project_noop_upsert_keeps_activity_time(self):
        project_dir = os.path.join(self.temp_dir, "stable-project")
        os.makedirs(project_dir)
        cm = self._create_config_manager()
        cm.upsert_project(project_dir, name="Stable")
        projects = cm.get_projects(include_hidden=True)
        projects[0]["updated_at"] = 123
        cm.set_projects(projects)

        cm.upsert_project(project_dir)

        self.assertEqual(cm.get_projects(include_hidden=True)[0]["updated_at"], 123)

    def test_explicit_empty_model_channels_survive_reload(self):
        cm = self._create_config_manager()
        cm.set_model_channels([], "")
        self.assertEqual(cm.get_model_channels(), [])
        self.assertEqual(cm.get_selected_model_id(), "")

        reloaded = self._create_config_manager()
        self.assertEqual(reloaded.get_model_channels(), [])
        self.assertEqual(reloaded.get_selected_model_id(), "")

    def test_project_config_migrates_hidden_to_archived(self):
        project_dir = os.path.join(self.temp_dir, "legacy-hidden")
        os.makedirs(project_dir)
        cm = self._create_config_manager(
            {
                "projects": [
                    {
                        "path": project_dir,
                        "name": "Legacy Hidden",
                        "hidden": True,
                    }
                ]
            }
        )

        project = cm.get_projects(include_hidden=True)[0]

        self.assertTrue(project["archived"])
        self.assertTrue(project["hidden"])
        self.assertEqual(cm.get_projects(include_hidden=False), [])

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

    def test_model_reasoning_efforts_are_normalized_and_remembered(self):
        cm = self._create_config_manager()
        channels = [{
            "channel_id": "gpt-channel",
            "display_name": "GPT 渠道",
            "provider_type": "openai",
            "api_key": "test-key",
            "base_url": "https://example.com/v1",
            "models": [{
                "id": "gpt-model",
                "display_name": "GPT-5.5",
                "model_name": "gpt-5.5",
                "reasoning_efforts": ["low", "medium", "high", "invalid", "high"],
                "reasoning_effort": "medium",
            }],
        }]

        cm.set_model_channels(channels, "gpt-model")
        self.assertEqual(cm.get_model_profile()["reasoning_efforts"], ["low", "medium", "high"])
        self.assertTrue(cm.set_model_reasoning_effort("gpt-model", "high"))
        self.assertEqual(cm.get_model_profile()["reasoning_effort"], "high")
        self.assertFalse(cm.set_model_reasoning_effort("gpt-model", "xhigh"))

    def test_non_reasoning_model_does_not_inherit_legacy_effort(self):
        cm = self._create_config_manager()
        cm.set_model_channels([{
            "channel_id": "plain-channel",
            "display_name": "普通渠道",
            "provider_type": "openai",
            "api_key": "test-key",
            "base_url": "https://example.com/v1",
            "models": [{"id": "plain-model", "model_name": "plain-model"}],
        }], "plain-model")

        profile = cm.get_model_profile()
        self.assertEqual(profile["reasoning_efforts"], [])
        self.assertEqual(profile["reasoning_effort"], "")

    def test_set_model_channels_falls_back_when_selected_model_is_removed(self):
        cm = self._create_config_manager()
        channels = [
            {
                "channel_id": "anthropic-channel",
                "display_name": "Anthropic Test",
                "provider_type": "anthropic",
                "api_key": "anthropic-key",
                "base_url": "https://anthropic.example",
                "models": [
                    {
                        "id": "anthropic-new",
                        "display_name": "Claude New",
                        "model_name": "claude-new",
                    }
                ],
            }
        ]

        cm.set_model_channels(channels, "removed-model")

        self.assertEqual(cm.get_selected_model_id(), "anthropic-new")
        self.assertEqual(cm.get("llm_provider"), "anthropic")
        self.assertEqual(cm.get("api_key"), "anthropic-key")
        self.assertEqual(cm.get("base_url"), "https://anthropic.example")
        self.assertEqual(cm.get("model_name"), "claude-new")

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

    def test_mcp_servers_are_normalized_and_persisted(self):
        cm = self._create_config_manager(
            {
                "mcp_servers": [
                    {
                        "name": "Filesystem MCP",
                        "enabled": True,
                        "transport": "stdio",
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "D:\\demo"],
                        "env": {"NODE_ENV": "production"},
                    },
                    {
                        "name": "Remote Docs",
                        "transport": "http",
                        "url": "https://example.com/mcp",
                        "headers": {"Authorization": "Bearer token"},
                        "timeout_seconds": 99,
                    },
                ]
            }
        )

        servers = cm.get_mcp_servers()

        self.assertEqual(len(servers), 2)
        self.assertEqual(servers[0]["transport"], "stdio")
        self.assertEqual(servers[0]["command"], "npx")
        self.assertEqual(servers[0]["env"]["NODE_ENV"], "production")
        self.assertEqual(servers[1]["transport"], "streamable_http")
        self.assertEqual(servers[1]["url"], "https://example.com/mcp")
        self.assertEqual(servers[1]["headers"]["Authorization"], "Bearer token")

        cm.set_mcp_servers(
            [
                {
                    "id": "remote-docs",
                    "name": "Remote Docs",
                    "enabled": False,
                    "transport": "streamable_http",
                    "url": "https://docs.example/mcp",
                    "headers": {"X-Token": "abc"},
                    "timeout_seconds": 45,
                }
            ]
        )
        stored = cm.get_mcp_servers()
        self.assertEqual(len(stored), 1)
        self.assertFalse(stored[0]["enabled"])
        self.assertEqual(stored[0]["id"], "remote-docs")
        self.assertEqual(stored[0]["timeout_seconds"], 45)

    def test_mcp_managed_auth_and_runtime_skill_are_preserved(self):
        servers = normalize_mcp_servers(
            [
                {
                    "id": "superset-mcp",
                    "name": "Superset MCP",
                    "transport": "streamable_http",
                    "url": "https://superset.example/mcp",
                    "runtime_skill": "superset-mcp",
                    "auth": {
                        "type": "superset_password",
                        "skill_name": "superset-mcp",
                        "password_field": "SUPERSET_PASSWORD",
                        "ignored": "drop-me",
                    },
                }
            ]
        )
        self.assertEqual(servers[0]["runtime_skill"], "superset-mcp")
        self.assertEqual(servers[0]["auth"]["type"], "superset_password")
        self.assertNotIn("ignored", servers[0]["auth"])

    def test_superset_managed_auth_logs_in_and_keeps_token_in_memory(self):
        def jwt(expiry):
            header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip("=")
            payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
            return f"{header}.{payload}.signature"

        class ConfigStub:
            def get_skill_config(self, _skill_name):
                return {
                    "SUPERSET_BASE_URL": "https://superset.example",
                    "SUPERSET_USERNAME": "admin",
                    "SUPERSET_PASSWORD": "secret",
                    "SUPERSET_PROVIDER": "ldap",
                }

        class Response:
            status_code = 200
            reason_phrase = "OK"

            def json(self):
                return {"access_token": jwt(int(time.time()) + 3600), "refresh_token": "refresh-secret"}

        server = {
            "id": "superset-mcp",
            "timeout_seconds": 30,
            "headers": {},
            "auth": {"type": "superset_password", "skill_name": "superset-mcp"},
        }
        clear_mcp_auth_cache()
        with patch("httpx.post", return_value=Response()) as post:
            first = prepare_mcp_server_config(server, ConfigStub())
            second = prepare_mcp_server_config(server, ConfigStub())
        self.assertEqual(post.call_count, 1)
        self.assertTrue(first["headers"]["Authorization"].startswith("Bearer "))
        self.assertEqual(first["headers"], second["headers"])
        self.assertEqual(server["headers"], {})
        self.assertEqual(post.call_args.kwargs["json"]["provider"], "ldap")

    def test_superset_refresh_401_reauthenticates_once(self):
        def jwt(expiry):
            header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip("=")
            payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
            return f"{header}.{payload}.signature"

        class ConfigStub:
            def get_skill_config(self, _skill_name):
                return {
                    "SUPERSET_BASE_URL": "https://superset.example",
                    "SUPERSET_USERNAME": "admin",
                    "SUPERSET_PASSWORD": "secret",
                }

        class Response:
            reason_phrase = "OK"

            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload
                if status_code == 401:
                    self.reason_phrase = "Unauthorized"

            def json(self):
                return self._payload

        expired_login = Response(
            200,
            {"access_token": jwt(int(time.time()) + 1), "refresh_token": "expired-refresh"},
        )
        refresh_401 = Response(401, {"message": "Token has expired"})
        fresh_login = Response(
            200,
            {"access_token": jwt(int(time.time()) + 3600), "refresh_token": "fresh-refresh"},
        )
        server = {
            "id": "superset-mcp-refresh",
            "timeout_seconds": 30,
            "auth": {"type": "superset_password", "skill_name": "superset-mcp"},
        }
        clear_mcp_auth_cache()
        with patch("httpx.post", side_effect=[expired_login, refresh_401, fresh_login]) as post:
            prepare_mcp_server_config(server, ConfigStub())
            prepared = prepare_mcp_server_config(server, ConfigStub())
        self.assertEqual(post.call_count, 3)
        self.assertTrue(prepared["headers"]["Authorization"].startswith("Bearer "))
        self.assertTrue(post.call_args_list[1].args[0].endswith("/api/v1/security/refresh"))

    def test_stdio_runtime_skill_uses_sandbox_environment(self):
        calls = {}

        class ServerParameters:
            def __init__(self, command, args, cwd, env):
                calls["params"] = {"command": command, "args": args, "cwd": cwd, "env": env}

        class Session:
            def __init__(self, _read, _write):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

            async def initialize(self):
                calls["initialized"] = True

        @asynccontextmanager
        async def stdio_client(_params):
            yield object(), object()

        mcp_module = types.ModuleType("mcp")
        mcp_module.ClientSession = Session
        mcp_module.StdioServerParameters = ServerParameters
        mcp_client_module = types.ModuleType("mcp.client")
        stdio_module = types.ModuleType("mcp.client.stdio")
        stdio_module.stdio_client = stdio_client

        async def exercise():
            with patch.dict(
                sys.modules,
                {"mcp": mcp_module, "mcp.client": mcp_client_module, "mcp.client.stdio": stdio_module},
            ), patch("core.sandbox_runtime.build_sandbox_env", return_value={"SANDBOX": "ready"}) as build_env:
                async with _open_mcp_session(
                    {
                        "transport": "stdio",
                        "command": "python",
                        "args": ["-m", "demo"],
                        "cwd": "D:\\demo",
                        "runtime_skill": "airflow",
                        "env": {"AIRFLOW_API_URL": "https://airflow.example"},
                    }
                ):
                    pass
                build_env.assert_called_once_with(workspace_dir="D:\\demo", skill_id="airflow")

        asyncio.run(exercise())
        self.assertTrue(calls["initialized"])
        self.assertEqual(calls["params"]["env"]["SANDBOX"], "ready")
        self.assertEqual(calls["params"]["env"]["AIRFLOW_API_URL"], "https://airflow.example")

    def test_upsert_mcp_servers_replaces_by_id(self):
        cm = self._create_config_manager(
            {
                "mcp_servers": [
                    {
                        "id": "showdoc",
                        "name": "Old ShowDoc",
                        "enabled": False,
                        "transport": "stdio",
                        "command": "npx",
                        "args": ["-y", "old"],
                    }
                ]
            }
        )

        summary = cm.upsert_mcp_servers(
            [
                {
                    "id": "showdoc",
                    "name": "ShowDoc MCP",
                    "enabled": False,
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "mcp-showdoc"],
                },
                {
                    "id": "superset-mcp",
                    "name": "Superset MCP",
                    "enabled": False,
                    "transport": "streamable_http",
                    "url": "http://localhost:5008/mcp",
                    "headers": {"Authorization": "Bearer token"},
                },
            ]
        )

        self.assertEqual(summary["added"], 1)
        self.assertEqual(summary["replaced"], 1)
        stored = cm.get_mcp_servers()
        self.assertEqual(len(stored), 2)
        self.assertEqual(stored[0]["name"], "ShowDoc MCP")
        self.assertEqual(stored[0]["args"], ["-y", "mcp-showdoc"])
        self.assertEqual(stored[1]["transport"], "streamable_http")

    def test_parse_mcp_servers_json_supports_named_mcpservers(self):
        payload = parse_mcp_servers_json(
            """
            {
              "mcpServers": {
                "showdoc": {
                  "type": "streamable-http",
                  "url": "https://www.showdoc.com.cn/mcp.php",
                  "headers": {
                    "Authorization": "Bearer token"
                  }
                }
              }
            }
            """
        )
        servers = normalize_mcp_servers(payload)

        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["id"], "showdoc")
        self.assertEqual(servers[0]["name"], "showdoc")
        self.assertEqual(servers[0]["transport"], "streamable_http")
        self.assertEqual(servers[0]["headers"]["Authorization"], "Bearer token")

    def test_parse_mcp_servers_json_supports_mcp_servers_array(self):
        payload = parse_mcp_servers_json(
            {
                "mcp_servers": [
                    {
                        "name": "Filesystem MCP",
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                        "env": [{"key": "NODE_ENV", "value": "production"}],
                    }
                ]
            }
        )
        servers = normalize_mcp_servers(payload)

        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["transport"], "stdio")
        self.assertEqual(servers[0]["command"], "npx")
        self.assertEqual(servers[0]["env"]["NODE_ENV"], "production")

    def test_parse_mcp_servers_json_supports_direct_server_array(self):
        payload = parse_mcp_servers_json(
            [
                {
                    "id": "remote-docs",
                    "name": "Remote Docs",
                    "transport": "http",
                    "url": "https://docs.example/mcp",
                }
            ]
        )
        servers = normalize_mcp_servers(payload)

        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["id"], "remote-docs")
        self.assertEqual(servers[0]["transport"], "streamable_http")
        self.assertEqual(servers[0]["url"], "https://docs.example/mcp")

    def test_parse_mcp_servers_json_rejects_invalid_payload(self):
        with self.assertRaisesRegex(ValueError, "Invalid MCP JSON"):
            parse_mcp_servers_json("{bad json}")

        with self.assertRaisesRegex(ValueError, "must contain `mcpServers`, `mcp_servers`, or a server list"):
            parse_mcp_servers_json({"unexpected": {"value": True}})

    def test_describe_mcp_import_error_reports_missing_dependency(self):
        self.assertIn(
            "not installed",
            describe_mcp_import_error(ModuleNotFoundError("No module named 'mcp'", name="mcp")),
        )
        self.assertIn(
            "Missing module: httpx_sse",
            describe_mcp_import_error(ModuleNotFoundError("No module named 'httpx_sse'", name="httpx_sse")),
        )

    def test_superset_connection_refusal_unwraps_exception_group(self):
        grouped = ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [ConnectionRefusedError("[WinError 10061] No connection could be made")],
        )
        error = describe_mcp_operation_error(
            {
                "source_skill": "superset-mcp",
                "url": "https://192.168.239.143:5008/mcp",
            },
            McpOperationError("transport", grouped),
        )

        self.assertIn("无法连接远程 Superset MCP 服务", error)
        self.assertIn("superset mcp run", error)
        self.assertNotIn("TaskGroup", error)

    def test_mcp_tls_error_reports_connection_stage(self):
        error = describe_mcp_operation_error(
            {"url": "https://superset.example:5008/mcp"},
            McpOperationError("transport", RuntimeError("SSL: WRONG_VERSION_NUMBER")),
        )

        self.assertIn("TLS", error)
        self.assertIn("transport", error)

    def test_open_streamable_http_transport_prefers_new_api(self):
        calls = {}

        class FakeAsyncClient:
            def __init__(self, headers=None, follow_redirects=None, timeout=None):
                calls["client_init"] = {
                    "headers": headers,
                    "follow_redirects": follow_redirects,
                    "timeout": timeout,
                }

            async def __aenter__(self):
                calls["client_entered"] = True
                return self

            async def __aexit__(self, exc_type, exc, tb):
                calls["client_exited"] = True

        async def _new_client(url, http_client=None):
            calls["new_api"] = {"url": url, "http_client": http_client}
            yield ("read", "write", "session")

        async def _old_client(*args, **kwargs):
            raise AssertionError("legacy streamablehttp_client should not be used when new API exists")

        fake_streamable_module = types.ModuleType("mcp.client.streamable_http")
        fake_streamable_module.streamable_http_client = asynccontextmanager(_new_client)
        fake_streamable_module.streamablehttp_client = asynccontextmanager(_old_client)
        fake_httpx_module = types.ModuleType("httpx")
        fake_httpx_module.AsyncClient = FakeAsyncClient

        async def _exercise():
            async with _open_streamable_http_transport("https://example.com/mcp", {"Authorization": "Bearer token"}, 12) as streams:
                self.assertEqual(streams, ("read", "write", "session"))

        with patch.dict(
            sys.modules,
            {
                "mcp.client.streamable_http": fake_streamable_module,
                "httpx": fake_httpx_module,
            },
        ):
            asyncio.run(_exercise())

        self.assertEqual(calls["client_init"]["headers"]["Authorization"], "Bearer token")
        self.assertEqual(calls["client_init"]["timeout"], 12)
        self.assertEqual(calls["new_api"]["url"], "https://example.com/mcp")
        self.assertTrue(calls["client_entered"])
        self.assertTrue(calls["client_exited"])

    def test_open_streamable_http_transport_falls_back_to_legacy_api(self):
        calls = {}

        async def _old_client(url, headers=None, timeout=None, sse_read_timeout=None):
            calls["legacy_api"] = {
                "url": url,
                "headers": headers,
                "timeout": timeout,
                "sse_read_timeout": sse_read_timeout,
            }
            yield ("legacy-read", "legacy-write", "legacy-session")

        fake_streamable_module = types.ModuleType("mcp.client.streamable_http")
        fake_streamable_module.streamablehttp_client = asynccontextmanager(_old_client)

        async def _exercise():
            async with _open_streamable_http_transport("https://legacy.example/mcp", {"X-Token": "abc"}, 18) as streams:
                self.assertEqual(streams, ("legacy-read", "legacy-write", "legacy-session"))

        with patch.dict(
            sys.modules,
            {"mcp.client.streamable_http": fake_streamable_module},
        ):
            asyncio.run(_exercise())

        self.assertEqual(calls["legacy_api"]["url"], "https://legacy.example/mcp")
        self.assertEqual(calls["legacy_api"]["headers"]["X-Token"], "abc")
        self.assertEqual(calls["legacy_api"]["timeout"], 18)
        self.assertEqual(calls["legacy_api"]["sse_read_timeout"], 18)

    def test_legacy_sop_templates_are_cleared_and_tasks_are_parked(self):
        cm = self._create_config_manager(
            {
                "sop_templates": [
                    {"id": "office-flow", "name": "办公流程", "steps": [{"title": "确认目标"}]},
                ],
                "automation_tasks": [
                    {"id": "task-1", "name": "旧任务", "template_id": "office-flow", "schedule_type": "manual"}
                ],
            }
        )

        self.assertEqual(cm.config.get("sop_templates"), [])
        tasks = cm.get_automation_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], "task-1")
        self.assertFalse(tasks[0]["enabled"])
        self.assertIn("旧版 SOP", tasks[0]["migration_note"])

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

    def test_get_resource_dir_uses_pyinstaller_runtime_root(self):
        with patch.object(env_utils.sys, "frozen", True, create=True), \
             patch.object(env_utils.sys, "_MEIPASS", r"C:\app\_internal", create=True):
            self.assertEqual(
                env_utils.get_resource_dir(),
                os.path.abspath(r"C:\app\_internal"),
            )

    def test_get_resource_dir_rejects_missing_frozen_runtime_root(self):
        with patch.object(env_utils.sys, "frozen", True, create=True), \
             patch.object(env_utils.sys, "_MEIPASS", "", create=True):
            with self.assertRaisesRegex(RuntimeError, "PyInstaller"):
                env_utils.get_resource_dir()

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
             patch.object(env_utils, "_sandbox_can_import", return_value=False):
            with self.assertRaises(RuntimeError) as cm:
                env_utils.ensure_package_installed("openpyxl")
            self.assertIn("bundled Python runtime is missing", str(cm.exception))

    def test_ensure_package_installed_uses_sandbox_probe_instead_of_host_import(self):
        env_utils._INSTALL_FAILED.clear()
        env_utils._INSTALL_SUCCESS.clear()
        with patch.object(env_utils, "get_python_executable", return_value="C:\\runtime\\python.exe"), \
             patch.object(env_utils, "_inject_skill_python_path"), \
             patch.object(env_utils, "_sandbox_can_import", side_effect=[False, True, True]) as sandbox_probe, \
             patch("core.sandbox_runtime.install_skill_dependencies", return_value={"ok": True, "installed": True, "message": "ok"}) as installer, \
             patch("core.sandbox_runtime.build_sandbox_env", return_value={"PYTHONPATH": "C:\\sandbox\\site-packages"}), \
             patch.object(env_utils, "_refresh_sys_path"), \
             patch.object(env_utils, "_attach_external_site_packages"), \
             patch.object(env_utils.importlib, "import_module", return_value=object()):
            env_utils.ensure_package_installed("Pillow", "PIL", skill_id="python-runner")

        self.assertEqual(installer.call_count, 1)
        self.assertEqual(sandbox_probe.call_count, 3)
        self.assertIn("python-runner:PIL", env_utils._INSTALL_SUCCESS)

    def test_ensure_package_installed_forces_reinstall_when_cached_skill_dependency_is_stale(self):
        env_utils._INSTALL_FAILED.clear()
        env_utils._INSTALL_SUCCESS.clear()
        with patch.object(env_utils, "get_python_executable", return_value="C:\\runtime\\python.exe"), \
             patch.object(env_utils, "_inject_skill_python_path"), \
             patch.object(env_utils, "_sandbox_can_import", side_effect=[False, False, True]) as sandbox_probe, \
             patch("core.sandbox_runtime.install_skill_dependencies", side_effect=[
                 {"ok": True, "installed": False, "message": "Dependencies already installed."},
                 {"ok": True, "installed": True, "message": "reinstalled"},
             ]) as installer, \
             patch("core.sandbox_runtime.build_sandbox_env", return_value={"PYTHONPATH": "C:\\sandbox\\site-packages"}), \
             patch.object(env_utils, "_refresh_sys_path"), \
             patch.object(env_utils, "_attach_external_site_packages"), \
             patch.object(env_utils.importlib, "import_module", side_effect=ImportError()):
            env_utils.ensure_package_installed("Pillow", "PIL", skill_id="python-runner")

        self.assertEqual(sandbox_probe.call_count, 3)
        self.assertEqual(installer.call_args_list[1].kwargs["force"], True)
        self.assertIn("python-runner:PIL", env_utils._INSTALL_SUCCESS)

    def test_sandbox_import_probe_returns_traceback_details(self):
        completed = subprocess.CompletedProcess(
            args=["python"],
            returncode=1,
            stdout='{"ok": false, "error": "Traceback\\nImportError: boom"}\n',
            stderr="",
        )
        with patch("core.sandbox_runtime.build_sandbox_env", return_value={"PYTHONPATH": "C:\\sandbox\\site-packages"}), \
             patch("subprocess.run", return_value=completed):
            probe = env_utils._sandbox_import_probe("C:\\runtime\\python.exe", "markitdown", skill_id="python-runner")

        self.assertFalse(probe["ok"])
        self.assertIn("ImportError: boom", probe["error"])

    def test_ensure_package_installed_includes_import_probe_details_on_failure(self):
        env_utils._INSTALL_FAILED.clear()
        env_utils._INSTALL_SUCCESS.clear()
        with patch.object(env_utils, "get_python_executable", return_value="C:\\runtime\\python.exe"), \
             patch.object(env_utils, "_inject_skill_python_path"), \
             patch.object(env_utils, "_sandbox_can_import", side_effect=[False, False, False]), \
             patch("core.sandbox_runtime.install_skill_dependencies", side_effect=[
                 {"ok": True, "installed": False, "message": "Dependencies already installed."},
                 {"ok": True, "installed": True, "message": "reinstalled"},
             ]), \
             patch("core.sandbox_runtime.build_sandbox_env", return_value={"PYTHONPATH": "C:\\sandbox\\site-packages"}), \
             patch.object(env_utils, "_refresh_sys_path"), \
             patch.object(env_utils, "_attach_external_site_packages"), \
             patch.object(env_utils, "_sandbox_import_probe", return_value={
                 "ok": False,
                 "returncode": 1,
                 "stdout": "",
                 "stderr": "",
                 "error": "Traceback\\nImportError: DLL load failed",
             }), \
             patch.object(env_utils.importlib, "import_module", side_effect=ImportError()):
            with self.assertRaises(RuntimeError) as cm:
                env_utils.ensure_package_installed("markitdown", "markitdown", skill_id="python-runner")

        self.assertIn("still cannot import markitdown", str(cm.exception))
        self.assertIn("DLL load failed", str(cm.exception))

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
    def get_tools_for_skill(self, _skill_name):
        return []
    def get_tool_record(self, _tool_name):
        return None
    def get_skill_of_tool(self, _tool_name):
        return None

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
    def test_daemon_shutdown_is_handled_by_qt_thread_poll(self):
        app = MagicMock()
        server = types.SimpleNamespace(shutdown_requested=True)
        self.assertTrue(daemon_module._poll_daemon_shutdown(app, server))
        app.quit.assert_called_once_with()

    def test_daemon_shutdown_poll_is_inert_while_running(self):
        app = MagicMock()
        server = types.SimpleNamespace(shutdown_requested=False)
        self.assertFalse(daemon_module._poll_daemon_shutdown(app, server))
        app.quit.assert_not_called()

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state = DaemonState(_DaemonConfigStub(self.temp_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_is_context_overflow_error(self):
        self.assertTrue(self.state._is_context_overflow_error({"error": "maximum context length exceeded"}))
        self.assertFalse(self.state._is_context_overflow_error({"error": "network timeout"}))

    def test_request_messages_prefers_ui_snapshot_over_sqlite(self):
        session_id = "desktop-session"
        self.state.chat_storage.save_conversation(
            session_id,
            [{"role": "user", "content": "old sqlite prompt"}],
            title="old",
        )
        snapshot = [
            {"role": "user", "content": "fresh prompt"},
            {"role": "assistant", "content": "fresh answer"},
        ]

        messages = self.state.request_messages(session_id, snapshot)

        self.assertEqual([msg.get("content") for msg in messages], ["fresh prompt", "fresh answer"])
        self.assertEqual([msg.get("content") for msg in self.state.sessions[session_id]], ["fresh prompt", "fresh answer"])

    def test_run_llm_sync_uses_snapshot_and_dedupes_current_user_message(self):
        session_id = "desktop-dedupe"
        snapshot = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "continue"},
        ]
        captured = {}

        def run_once(sid, worker_messages, workspace_dir, run_context=None):
            captured["messages"] = list(worker_messages)
            return {"generated_messages": [{"role": "assistant", "content": "done"}]}

        self.state._run_worker_once = run_once

        self.state.run_llm_sync(
            session_id,
            "continue",
            workspace_dir=self.temp_dir,
            run_context={},
            messages_snapshot=snapshot,
        )

        self.assertEqual([msg.get("content") for msg in captured["messages"]], ["first", "reply", "continue"])
        saved_messages = self.state.chat_storage.get_messages(session_id)
        self.assertEqual([msg.get("content") for msg in saved_messages], ["first", "reply", "continue", "done"])

    def test_save_session_uses_shared_persistence_filter(self):
        session_id = "daemon-persistence-filter"
        self.state.sessions[session_id] = [
            {"id": "u1", "role": "user", "content": "问题"},
            {
                "id": "skill-context",
                "role": "system",
                "content": "runtime only",
                "meta": {
                    "kind": "skill_context",
                    "source": "skill_prompt_query_match",
                },
            },
            {"id": "a1", "role": "assistant", "content": "回答"},
        ]

        self.state.save_session(session_id)

        self.assertEqual(
            [message["id"] for message in self.state.sessions[session_id]],
            ["u1", "skill-context", "a1"],
        )
        self.assertEqual(
            [message["id"] for message in self.state.chat_storage.get_messages(session_id)],
            ["u1", "skill-context", "a1"],
        )

    def test_snapshot_restores_context_after_idle_suspend(self):
        session_id = "desktop-after-idle"
        self.state.sessions[session_id] = [{"role": "user", "content": "stale memory"}]
        self.state.last_activity = 0
        self.state.idle_timeout = 1
        self.state.maybe_suspend()
        self.assertNotIn(session_id, self.state.sessions)
        snapshot = [
            {"role": "user", "content": "fresh after idle"},
            {"role": "assistant", "content": "still here"},
        ]

        messages = self.state.request_messages(session_id, snapshot)

        self.assertEqual([msg.get("content") for msg in messages], ["fresh after idle", "still here"])

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

    def test_gpt_5_6_uses_documented_context_budget(self):
        state = DaemonState(
            _DaemonConfigStub(
                self.temp_dir,
                values={"context_budget_ratio": 0.8},
                profile={"model_name": "gpt-5.6-terra"},
            )
        )

        self.assertEqual(state._context_window_tokens({"selected_model_id": "openai-default"}), 1050000)
        self.assertEqual(state._context_budget_threshold({"selected_model_id": "openai-default"}), 840000)

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
    def _build_prompt_worker(self, temp_dir):
        with patch("core.agent.SkillManager", return_value=_PromptSkillManagerStub()):
            worker = LLMWorker(
                [],
                _DaemonConfigStub(temp_dir),
                workspace_dir=temp_dir,
                session_id="session-1",
                conversation_id="conversation-1",
                run_context={"mode": RUN_MODE_EXECUTION},
            )
        worker._prompt_context_date = "2026-06-16"
        return worker

    def test_request_prompt_appends_runtime_context_to_the_ledger(self):
        temp_dir = tempfile.mkdtemp()
        try:
            worker = self._build_prompt_worker(temp_dir)
            current_messages = [
                {"role": "system", "content": worker._build_stable_system_prompt()},
                {"role": "user", "content": "hello"},
            ]
            runtime_prompt = worker._build_runtime_context_prompt(
                {
                    "python": {"available": True, "version": "3.a", "path": "python-a.exe"},
                    "node": {"available": False},
                    "bash": {"available": False},
                    "available_packages": ["stale-package"],
                    "missing_packages": ["another-stale-package"],
                }
            )
            generated_messages = []
            runtime_message = worker._append_runtime_context(
                runtime_prompt,
                current_messages,
                generated_messages,
            )
            request_messages = worker._build_request_messages(current_messages)

            self.assertEqual(request_messages[0]["content"], current_messages[0]["content"])
            self.assertEqual(request_messages[-1]["role"], "system")
            self.assertIn("# 当前运行状态", request_messages[-1]["content"])
            self.assertIn("应用 Python 路径:", runtime_prompt)
            self.assertIn("沙盒 Python 版本: 3.a", runtime_prompt)
            self.assertIn("Node.js 不随应用分发", runtime_prompt)
            self.assertNotIn("JavaScript、JSON 和前端脚本可优先使用", runtime_prompt)
            self.assertNotIn("运行时库检测", runtime_prompt)
            self.assertNotIn("stale-package", runtime_prompt)
            self.assertEqual(len(current_messages), 3)
            self.assertEqual(generated_messages, [runtime_message])
            self.assertEqual(runtime_message["meta"]["kind"], "runtime_context")
            self.assertTrue(runtime_message["meta"]["hidden"])

            self.assertIsNone(
                worker._append_runtime_context(
                    runtime_prompt,
                    current_messages,
                    generated_messages,
                )
            )
            self.assertEqual(request_messages, current_messages)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_request_prefix_diagnostic_rejects_history_rewrites(self):
        temp_dir = tempfile.mkdtemp()
        try:
            worker = self._build_prompt_worker(temp_dir)
            events = []
            worker.observability_signal.connect(events.append)
            previous = [
                {"role": "system", "content": "stable"},
                {"role": "user", "content": "hello"},
            ]
            current = previous + [{"role": "assistant", "content": "done"}]

            self.assertEqual(
                worker._verify_request_prefix(previous, current, "chat_completions"),
                current,
            )
            self.assertTrue(events[-1]["ok"])
            self.assertEqual(events[-1]["matched_message_count"], 2)

            rewritten = [dict(previous[0]), {"role": "user", "content": "changed"}]
            with self.assertRaisesRegex(RuntimeError, "first_difference_index=1"):
                worker._verify_request_prefix(previous, rewritten, "chat_completions")
            self.assertFalse(events[-1]["ok"])
            self.assertEqual(events[-1]["first_difference_index"], 1)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_previous_provider_prefix_is_recovered_across_worker_restarts(self):
        temp_dir = tempfile.mkdtemp()
        try:
            worker = self._build_prompt_worker(temp_dir)
            request_messages = [
                {"role": "system", "content": "stable"},
                {"role": "user", "content": "first"},
                {
                    "role": "system",
                    "content": "runtime v1",
                    "meta": {
                        "hidden": True,
                        "kind": "runtime_context",
                        "ledger_revision": 1,
                    },
                },
                {"role": "assistant", "content": "first answer"},
                {"role": "user", "content": "second"},
                {
                    "role": "system",
                    "content": "runtime v2",
                    "meta": {"hidden": True, "kind": "runtime_context_update"},
                },
            ]
            sanitized = [
                {key: value for key, value in message.items() if key != "meta"}
                for message in request_messages
            ]

            recovered = worker._previous_request_prefix_from_ledger(
                request_messages,
                sanitized,
            )

            self.assertEqual(recovered, sanitized[:3])
            self.assertEqual(
                worker._verify_request_prefix(recovered, sanitized, "chat_completions"),
                sanitized,
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_stable_system_prompt_ignores_runtime_snapshot_changes(self):
        temp_dir = tempfile.mkdtemp()
        try:
            worker = self._build_prompt_worker(temp_dir)
            worker._stable_system_prompt = None
            first = worker._build_stable_system_prompt()
            worker.tools = [{"type": "function", "function": {"name": "bash"}}]
            worker._prompt_context_date = "2026-06-17"
            second = worker._build_stable_system_prompt()

            self.assertEqual(first, second)
            self.assertNotIn("当前日期", first)
            self.assertNotIn("当前可用工具清单（仅以下工具真正暴露给你，可直接调用）", first)
            self.assertNotIn("JavaScript、JSON 和前端脚本可优先使用", first)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_clarification_count_is_dynamic_not_cached_in_stable_prompt(self):
        temp_dir = tempfile.mkdtemp()
        try:
            worker = self._build_prompt_worker(temp_dir)
            snapshot = {
                "python": {"available": True, "version": "3.test", "path": "python.exe"},
                "node": {"available": False},
                "bash": {"available": False},
            }
            stable_prompt = worker._build_stable_system_prompt()
            worker.run_context["clarify_round_count"] = 1
            first_runtime = worker._build_runtime_context_prompt(snapshot)
            worker.run_context["clarify_round_count"] = 2
            second_runtime = worker._build_runtime_context_prompt(snapshot)

            self.assertNotIn("当前任务已澄清", stable_prompt)
            self.assertIn("当前任务已澄清 1/3 轮", first_runtime)
            self.assertIn("当前任务已澄清 2/3 轮", second_runtime)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_stable_system_prompt_is_frozen_per_worker(self):
        temp_dir = tempfile.mkdtemp()
        try:
            worker = self._build_prompt_worker(temp_dir)
            worker._stable_system_prompt = None
            first = worker._get_stable_system_prompt()
            memories_path = os.path.join(temp_dir, "memories.md")
            with open(memories_path, "w", encoding="utf-8") as handle:
                handle.write("new memory that should wait for the next worker")
            second = worker._get_stable_system_prompt()

            self.assertEqual(first, second)
            self.assertNotIn("new memory", second)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_prompt_observability_omits_hash_fields(self):
        temp_dir = tempfile.mkdtemp()
        try:
            worker = self._build_prompt_worker(temp_dir)
            events = []
            worker.observability_signal = type(
                "_Signal",
                (),
                {"emit": lambda _self, payload: events.append(payload)},
            )()

            worker._emit_prompt_observability(
                "stable",
                "runtime",
                [{"role": "system", "content": "stable"}, {"role": "system", "content": "runtime"}],
            )

            self.assertEqual(events[0]["prompt_cache_key"], "conversation-1")
            self.assertNotIn("stable_prompt_hash", events[0])
            self.assertNotIn("runtime_context_hash", events[0])
            self.assertNotIn("tools_hash", events[0])
            self.assertNotIn("message_prefix_hash", events[0])
            self.assertEqual(events[1]["type"], "tool_exposure")
            self.assertEqual(
                set(events[1]["groups"]),
                {
                    "core_builtin_direct",
                    "session_selected",
                    "tool_search_discovered",
                    "other_direct",
                },
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_prompt_observability_reports_tool_exposure_sources(self):
        temp_dir = tempfile.mkdtemp()
        try:
            worker = self._build_prompt_worker(temp_dir)

            class SkillManagerStub(_PromptSkillManagerStub):
                def get_tools_for_skill(self, skill_name):
                    return ["browser_skill_cli"] if skill_name == "browser-automation" else []

                def get_tool_record(self, tool_name):
                    if tool_name == "bash":
                        return {"source_kind": "core_builtin"}
                    return {"source_kind": "optional"}

                def get_skill_of_tool(self, tool_name):
                    return "command-tools" if tool_name == "bash" else "browser-automation"

            worker.skill_manager = SkillManagerStub()
            worker.run_context["selected_skill_names"] = ["browser-automation"]
            worker.discovered_tool_names = {"browser_skill_cli"}
            worker.tools = [
                {"type": "function", "function": {"name": "bash"}},
                {"type": "function", "function": {"name": "browser_skill_cli"}},
            ]
            events = []
            worker.observability_signal = type(
                "_Signal",
                (),
                {"emit": lambda _self, payload: events.append(payload)},
            )()

            worker._emit_prompt_observability("stable", "runtime", [])

            exposure = next(event for event in events if event.get("type") == "tool_exposure")
            self.assertEqual(exposure["groups"]["core_builtin_direct"], ["bash"])
            self.assertEqual(exposure["groups"]["session_selected"], ["browser_skill_cli"])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

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
                {
                    "python": {"available": True, "version": "3.test", "path": "python.exe"},
                    "node": {"available": False},
                    "bash": {"available": False},
                }
            )

            self.assertLess(prompt.index("策略 [持久化]:"), prompt.index("# 当前运行状态"))
            self.assertLess(prompt.index("策略 [能力暴露]:"), prompt.index("当前运行模式: execution"))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_system_prompt_describes_tool_layers_and_document_boundaries(self):
        temp_dir = tempfile.mkdtemp()
        try:
            worker = LLMWorker.__new__(LLMWorker)
            worker.workspace_dir = temp_dir
            worker.run_context = {"mode": RUN_MODE_EXECUTION}
            worker.tools = [
                {"type": "function", "function": {"name": "tool_search"}},
                {"type": "function", "function": {"name": "workspace_list_files"}},
                {"type": "function", "function": {"name": "text_file_read"}},
                {"type": "function", "function": {"name": "apply_patch"}},
                {"type": "function", "function": {"name": "run_python_code"}},
                {"type": "function", "function": {"name": "run_node_code"}},
            ]
            worker.parent_agent_id = ""
            worker.skill_manager = _PromptSkillManagerStub()
            worker.config_manager = _DaemonConfigStub(temp_dir)

            prompt = worker._build_system_prompt(
                {
                    "python": {"available": True, "version": "3.test", "path": "python.exe"},
                    "node": {"available": True, "version": "20.test", "path": "node.exe"},
                    "bash": {"available": False},
                }
            )

            self.assertIn("能力分层", prompt)
            self.assertIn("ai_skills", prompt)
            self.assertIn("可选能力", prompt)
            self.assertIn("核心内置 Tool 已直接出现在当前工具清单", prompt)
            self.assertIn("不要先用 'tool_search' 搜索这些内置 Tool", prompt)
            self.assertIn("workspace_list_files", prompt)
            self.assertIn("'glob' 只查路径", prompt)
            self.assertIn("text_file_read", prompt)
            self.assertIn("apply_patch", prompt)
            self.assertIn("文本内容", prompt)
            self.assertIn("唯一工具", prompt)
            self.assertIn("offset=1", prompt)
            self.assertIn("SHA-256", prompt)
            self.assertIn("禁止用 'grep' 的匹配结果代替完整读取", prompt)
            self.assertIn("*** Begin Patch", prompt)
            self.assertIn("*** End of File", prompt)
            self.assertIn("重复片段必须补足上下文", prompt)
            self.assertIn("删除会一次展示全部路径并要求确认", prompt)
            self.assertNotIn("text_file_" + "write", prompt)
            self.assertNotIn("text_file_" + "update", prompt)
            self.assertIn("document-reader", prompt)
            self.assertIn("document_read", prompt)
            self.assertIn("实际生成工具或运行时库", prompt)
            self.assertIn("run_python_code", prompt)
            self.assertIn("当前用户环境已检测到 Node.js（node.exe）", prompt)
            self.assertIn("JavaScript、JSON 和前端脚本可优先使用 'run_node_code'", prompt)
            self.assertIn("当前可用工具清单", prompt)
            self.assertNotIn("默认关闭的可选插件", prompt)
            self.assertNotIn("运行时库检测", prompt)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_workspace_tools_appear_in_prompt_without_workspace_binding(self):
        temp_dir = tempfile.mkdtemp()
        try:
            worker = self._build_prompt_worker(temp_dir)
            worker.run_context = {
                "mode": RUN_MODE_EXECUTION,
                "workspace_mode": "chat_only",
            }
            worker.workspace_dir = None
            worker.discovered_tool_names = set()
            worker.is_subagent = False
            worker.skill_manager = SkillManager(
                workspace_dir=None,
                config_manager=None,
                load_mcp_tools=False,
            )

            worker._refresh_tool_definitions()
            prompt = worker._build_system_prompt(
                {
                    "python": {"available": True, "version": "3.test", "path": "python.exe"},
                    "node": {"available": False},
                    "bash": {"available": False},
                }
            )

            visible_names = {
                item["function"]["name"]
                for item in worker.tools
                if isinstance(item, dict) and isinstance(item.get("function"), dict)
            }
            for name in (
                "workspace_list_files",
                "text_file_read",
                "apply_patch",
                "glob",
                "grep",
                "bash",
            ):
                self.assertIn(name, visible_names)
                self.assertIn(f"`{name}`", prompt)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_image_turn_keeps_tool_search_available(self):
        worker = LLMWorker.__new__(LLMWorker)
        worker.tools = [
            {"type": "function", "function": {"name": "tool_search"}},
            {"type": "function", "function": {"name": "text_file_read"}},
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
        self.assertEqual(tool_names, ["tool_search", "text_file_read"])


class TestLLMWorkerToolLoopGuard(unittest.TestCase):
    def test_repeated_tool_signature_stops_on_third_model_request(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                self.call_count = 0

            def get_tool_definitions(self, *args, **kwargs):
                return []

            def check_for_updates(self):
                return False

            def get_system_prompts(self, *args, **kwargs):
                return ""

            def get_brief_skill_prompt(self, skill_name):
                return ""

            def get_skill_display_name(self, skill_name):
                return skill_name

            def get_skill_of_tool(self, name):
                return ""

            def call_tool(self, name, args, context=None):
                self.call_count += 1
                return {"status": "ok", "content": f"result {self.call_count}"}

        class _ProviderStub:
            provider_name = "stub"
            model_name = "stub-model"
            base_url = ""
            thinking_enabled = False

            def chat_stream(self, messages, tools=None, prompt_cache_key=None):
                yield {
                    "type": "tool_call",
                    "index": 0,
                    "id": f"call-{len([m for m in messages if m.get('role') == 'assistant'])}",
                    "function": {"name": "read_file", "arguments": '{"path":"demo.txt"}'},
                }

        temp_dir = tempfile.mkdtemp()
        finished = []
        try:
            skill_manager_instances = []

            def _skill_manager_factory(*args, **kwargs):
                manager = _SkillManagerStub()
                skill_manager_instances.append(manager)
                return manager

            with (
                patch("core.agent.SkillManager", side_effect=_skill_manager_factory),
                patch("core.agent.LLMFactory.create_provider", return_value=_ProviderStub()),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "read demo"}],
                    _DaemonConfigStub(temp_dir, values={"api_key": "test-key"}),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION},
                )
                worker.finished_signal.connect(lambda data: finished.append(data))
                worker.run()

            self.assertTrue(finished)
            self.assertIn("连续 3 次重复的工具调用", finished[0]["content"])
            self.assertEqual(skill_manager_instances[0].call_count, 2)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_deepseek_responses_worker_persists_and_replays_items_across_tool_turn(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                self.call_count = 0

            def get_tool_definitions(self, *args, **kwargs):
                return []

            def check_for_updates(self):
                return False

            def get_system_prompts(self, *args, **kwargs):
                return ""

            def get_brief_skill_prompt(self, skill_name):
                return ""

            def get_skill_display_name(self, skill_name):
                return skill_name

            def get_skill_of_tool(self, name):
                return ""

            def call_tool(self, name, args, context=None):
                self.call_count += 1
                return {"status": "ok", "content": "tool result"}

        class _ProviderStub:
            provider_name = "DeepSeek Responses"
            model_name = "deepseek-v4-flash"
            base_url = "https://api.deepseek.com"
            thinking_enabled = True
            requires_deepseek_responses_replay = True

            def __init__(self):
                self.requests = []

            def chat_stream(self, messages, tools=None, prompt_cache_key=None):
                self.requests.append(messages)
                if len(self.requests) == 1:
                    yield {"type": "reasoning", "content": "先调用工具"}
                    yield {
                        "type": "server_tool_status",
                        "id": "ws-observe",
                        "name": "web_search",
                        "status": "searching",
                    }
                    yield {
                        "type": "tool_call",
                        "index": 0,
                        "id": "call-1",
                        "function": {"name": "read_file", "arguments": '{"path":"demo.txt"}'},
                    }
                    yield {
                        "type": "response_items",
                        "items": [
                            {
                                "id": "rs_1",
                                "type": "reasoning",
                                "summary": [],
                                "content": [{"type": "reasoning_text", "text": "先调用工具"}],
                            },
                            {
                                "id": "fc_1",
                                "type": "function_call",
                                "call_id": "call-1",
                                "name": "read_file",
                                "arguments": '{"path":"demo.txt"}',
                            },
                        ],
                    }
                    return
                yield {"type": "reasoning", "content": "整理结果"}
                yield {"type": "content", "content": "完成"}
                yield {
                    "type": "response_items",
                    "items": [
                        {
                            "id": "rs_2",
                            "type": "reasoning",
                            "summary": [],
                            "content": [{"type": "reasoning_text", "text": "整理结果"}],
                        },
                        {
                            "id": "msg_2",
                            "type": "message",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "完成", "annotations": []}],
                        },
                    ],
                }

        temp_dir = tempfile.mkdtemp()
        finished = []
        observability = []
        provider = _ProviderStub()
        try:
            with (
                patch("core.agent.SkillManager", side_effect=_SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=provider),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "read demo"}],
                    _DaemonConfigStub(temp_dir, values={"api_key": "test-key"}),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION},
                )
                worker.finished_signal.connect(lambda data: finished.append(data))
                worker.observability_signal.connect(lambda data: observability.append(data))
                worker.run()

            self.assertEqual(len(provider.requests), 2)
            self.assertEqual(
                provider.requests[1][:len(provider.requests[0])],
                provider.requests[0],
            )
            replayed_assistant = next(
                message
                for message in provider.requests[1]
                if message.get("role") == "assistant" and message.get("tool_calls")
            )
            self.assertEqual(
                replayed_assistant[DEEPSEEK_RESPONSES_REPLAY_INPUT_KEY][0]["id"],
                "rs_1",
            )
            first_generated_assistant = next(
                message
                for message in finished[0]["generated_messages"]
                if message.get("role") == "assistant" and message.get("tool_calls")
            )
            self.assertEqual(
                first_generated_assistant["meta"][DEEPSEEK_RESPONSES_REPLAY_META_KEY][1]["call_id"],
                "call-1",
            )
            self.assertTrue(any(
                event.get("type") == "server_tool_status"
                and event.get("status") == "searching"
                for event in observability
            ))
            self.assertEqual(finished[0]["content"], "完成")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_standard_responses_worker_persists_and_replays_items_with_strict_prefix(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return []

            def get_system_prompts(self, *args, **kwargs):
                return ""

            def get_skill_of_tool(self, _name):
                return ""

            def call_tool(self, name, args, context=None):
                return {"status": "ok", "content": "tool result"}

        class _ProviderStub:
            provider_name = "GPT Responses"
            model_name = "gpt-5.6"
            base_url = "https://api.openai.com/v1"
            api_protocol = "responses"
            thinking_enabled = True
            requires_responses_replay = True
            requires_deepseek_responses_replay = False

            def __init__(self):
                self.requests = []

            def chat_stream(self, messages, tools=None, prompt_cache_key=None):
                self.requests.append(messages)
                if len(self.requests) == 1:
                    yield {
                        "type": "tool_call",
                        "index": 0,
                        "id": "call-1",
                        "function": {"name": "read_file", "arguments": '{"path":"demo.txt"}'},
                    }
                    yield {
                        "type": "response_items",
                        "items": [{
                            "id": "fc_1",
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "read_file",
                            "arguments": '{"path":"demo.txt"}',
                        }],
                    }
                    return
                yield {"type": "content", "content": "完成"}
                yield {
                    "type": "response_items",
                    "items": [{
                        "id": "msg_2",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "完成", "annotations": []}],
                    }],
                }

        from core.llm.responses_replay import (
            RESPONSES_REPLAY_INPUT_KEY,
            RESPONSES_REPLAY_META_KEY,
        )

        temp_dir = tempfile.mkdtemp()
        finished = []
        provider = _ProviderStub()
        try:
            with (
                patch("core.agent.SkillManager", side_effect=_SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=provider),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "read demo"}],
                    _DaemonConfigStub(temp_dir, values={"api_key": "test-key"}),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION},
                )
                worker.finished_signal.connect(finished.append)
                worker.run()

            self.assertEqual(len(provider.requests), 2)
            self.assertEqual(
                provider.requests[1][:len(provider.requests[0])],
                provider.requests[0],
            )
            replayed_assistant = next(
                message for message in provider.requests[1]
                if message.get("role") == "assistant" and message.get("tool_calls")
            )
            self.assertEqual(replayed_assistant[RESPONSES_REPLAY_INPUT_KEY][0]["id"], "fc_1")
            generated_assistant = next(
                message for message in finished[0]["generated_messages"]
                if message.get("role") == "assistant" and message.get("tool_calls")
            )
            self.assertEqual(
                generated_assistant["meta"][RESPONSES_REPLAY_META_KEY][0]["call_id"],
                "call-1",
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_responses_stop_keeps_partial_text_out_of_provider_ledger(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return []

            def get_system_prompts(self, *args, **kwargs):
                return ""

        class _ProviderStub:
            provider_name = "GPT Responses"
            model_name = "gpt-5.6"
            base_url = "https://api.openai.com/v1"
            api_protocol = "responses"
            thinking_enabled = False
            requires_responses_replay = True
            requires_deepseek_responses_replay = False

            def __init__(self):
                self.worker = None

            def chat_stream(self, messages, tools=None, prompt_cache_key=None):
                yield {"type": "content", "content": "partial"}
                self.worker.is_stopped = True

        temp_dir = tempfile.mkdtemp()
        finished = []
        provider = _ProviderStub()
        try:
            with (
                patch("core.agent.SkillManager", side_effect=_SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=provider),
            ):
                worker = LLMWorker(
                    [{"role": "user", "content": "start"}],
                    _DaemonConfigStub(temp_dir, values={"api_key": "test-key"}),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION},
                )
                provider.worker = worker
                worker.finished_signal.connect(finished.append)
                worker.run()

            self.assertEqual(finished[0]["content"], "partial")
            generated = finished[0]["generated_messages"]
            self.assertFalse(any(message.get("role") == "assistant" for message in generated))
            stopped = next(
                message for message in generated
                if (message.get("meta") or {}).get("source") == "responses_generation_stopped"
            )
            self.assertTrue(stopped["meta"]["hidden"])
            self.assertNotIn("partial", stopped["content"])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_chat_completions_prefix_survives_worker_restart(self):
        class _SkillManagerStub:
            def __init__(self, *_args, **_kwargs):
                pass

            def get_tool_definitions(self, *args, **kwargs):
                return []

            def get_system_prompts(self, *args, **kwargs):
                return ""

        class _ProviderStub:
            provider_name = "Chat Completions"
            model_name = "chat-model"
            base_url = "https://chat.example/v1"
            api_protocol = "chat_completions"
            thinking_enabled = False
            requires_responses_replay = False
            requires_deepseek_responses_replay = False

            def __init__(self):
                self.requests = []

            def chat_stream(self, messages, tools=None, prompt_cache_key=None):
                self.requests.append(messages)
                yield {"type": "content", "content": f"answer-{len(self.requests)}"}

        temp_dir = tempfile.mkdtemp()
        provider = _ProviderStub()
        try:
            with (
                patch("core.agent.SkillManager", side_effect=_SkillManagerStub),
                patch("core.agent.LLMFactory.create_provider", return_value=provider),
            ):
                first_finished = []
                first_messages = [{"id": "user-1", "role": "user", "content": "first"}]
                first_worker = LLMWorker(
                    first_messages,
                    _DaemonConfigStub(temp_dir, values={"api_key": "test-key"}),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION},
                )
                first_worker.finished_signal.connect(first_finished.append)
                first_worker.run()

                restored_ledger = first_messages + first_finished[0]["generated_messages"]
                restored_ledger.append({"id": "user-2", "role": "user", "content": "second"})
                second_worker = LLMWorker(
                    restored_ledger,
                    _DaemonConfigStub(temp_dir, values={"api_key": "test-key"}),
                    workspace_dir=temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION},
                )
                second_worker.run()

            self.assertEqual(len(provider.requests), 2)
            self.assertEqual(
                provider.requests[1][:len(provider.requests[0])],
                provider.requests[0],
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


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

    def test_notify_existing_ui_with_retries_waits_for_booting_server(self):
        attempts = []

        def fake_notify(*_args, **_kwargs):
            attempts.append(time.time())
            return len(attempts) >= 3

        with patch("core.single_instance.notify_existing_ui", side_effect=fake_notify):
            self.assertTrue(
                notify_existing_ui_with_retries(
                    "demo-server",
                    total_timeout_ms=400,
                    interval_ms=1,
                    per_attempt_timeout_ms=1,
                )
            )

        self.assertEqual(len(attempts), 3)


class TestLLMWorkerGuidance(unittest.TestCase):
    def _worker(self, turn_id="turn-1"):
        worker = LLMWorker.__new__(LLMWorker)
        QThread = __import__("PySide6.QtCore", fromlist=["QThread"]).QThread
        QThread.__init__(worker)
        worker.turn_id = turn_id
        worker.is_stopped = False
        worker._guidance_lock = threading.Lock()
        worker._pending_guidance = []
        worker._guidance_open = True
        return worker

    def test_steer_queues_and_applies_messages_in_fifo_order(self):
        worker = self._worker()
        first = {"id": "g1", "role": "user", "content": "first"}
        second = {"id": "g2", "role": "user", "content": "second"}

        self.assertTrue(worker.steer(first, "turn-1")["accepted"])
        self.assertTrue(worker.steer(second, "turn-1")["accepted"])
        current_messages = []
        generated_messages = []
        self.assertTrue(worker._append_pending_guidance(current_messages, generated_messages))

        self.assertEqual([item["id"] for item in current_messages], ["g1", "g2"])
        self.assertEqual(current_messages, generated_messages)

    def test_steer_rejects_mismatched_or_closed_turn(self):
        worker = self._worker()
        message = {"role": "user", "content": "guide"}

        self.assertEqual(worker.steer(message, "other")["error"], "turn_mismatch")
        worker._take_pending_guidance(close=True)
        self.assertEqual(worker.steer(message, "turn-1")["error"], "turn_not_active")


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

    def test_steer_message_roundtrip_checks_active_turn(self):
        class _Worker:
            turn_id = "turn-7"

            def steer(self, message, expected_turn_id=None):
                return {
                    "accepted": expected_turn_id == self.turn_id,
                    "turn_id": self.turn_id,
                    "content": message.get("content"),
                }

        self.state.set_active_worker("session-7", _Worker(), turn_id="turn-7")
        accepted = self.client.steer_message(
            "session-7", "turn-7", {"role": "user", "content": "focus tests"}
        )
        rejected = self.client.steer_message(
            "session-7", "stale-turn", {"role": "user", "content": "wrong"}
        )

        self.assertTrue(accepted["accepted"])
        self.assertEqual(accepted["turn_id"], "turn-7")
        self.assertFalse(rejected["accepted"])
        self.assertEqual(rejected["error"], "turn_mismatch")

    def test_stream_message_waits_for_worker_without_model_response_timeout(self):
        from PySide6.QtCore import QThread, Signal

        class _SlowStreamWorker(QThread):
            thinking_signal = Signal(str)
            content_signal = Signal(str)
            tool_call_signal = Signal(dict)
            tool_result_signal = Signal(dict)
            observability_signal = Signal(dict)
            agent_state_signal = Signal(dict)
            output_signal = Signal(str)
            finished_signal = Signal(object)

            def __init__(self, *args, **kwargs):
                super().__init__()
                self.stopped = False

            def stop(self):
                self.stopped = True

            def run(self):
                time.sleep(0.05)
                self.content_signal.emit("delayed")
                self.finished_signal.emit({"content": "done"})

        with patch("core.daemon.LLMWorker", _SlowStreamWorker), patch(
            "core.daemon.DAEMON_STREAM_RESPONSE_TIMEOUT_SEC",
            0.01,
            create=True,
        ):
            chunks = list(
                self.client.send_message_stream(
                    "session-stream-wait",
                    "hello",
                    workspace_dir=self.temp_dir,
                    run_context={"mode": RUN_MODE_EXECUTION},
                )
            )

        self.assertIn({"type": "content", "delta": "delayed"}, chunks)
        self.assertTrue(
            any(
                chunk.get("type") == "final"
                and chunk.get("result", {}).get("content") == "done"
                for chunk in chunks
            )
        )
        self.assertFalse(any(chunk.get("type") == "error" for chunk in chunks))

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

