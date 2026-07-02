import json
import os
import sys
import shutil
import re
import uuid
import time
from contextlib import contextmanager
from .env_utils import get_app_data_dir, get_base_dir
from .llm.deepseek import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_DEEPSEEK_REASONING_EFFORT,
    DEFAULT_DEEPSEEK_THINKING_ENABLED,
    normalize_reasoning_effort,
    normalize_reasoning_efforts,
    should_migrate_legacy_model,
)
from .automation_manager import (
    AUTOMATION_HISTORY_LIMIT,
    normalize_automation_history,
    normalize_automation_tasks,
)
from .mcp_client import DEFAULT_MCP_TIMEOUT_SECONDS, TRANSPORT_STDIO, normalize_mcp_transport
from .runtime_components import default_download_sources, normalize_download_sources

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"


def _slug_config_value(value):
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-").lower()
    return text or uuid.uuid4().hex[:8]


def normalize_mcp_env_or_headers(value):
    if isinstance(value, dict):
        items = value.items()
    elif isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, dict):
                key = item.get("key") or item.get("name")
                if key is None:
                    continue
                items.append((key, item.get("value")))
    else:
        return {}
    normalized = {}
    for key, item in items:
        text_key = str(key or "").strip()
        if not text_key:
            continue
        normalized[text_key] = str(item if item is not None else "")
    return normalized


def normalize_mcp_string_list(value):
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        text = str(item or "").strip()
        if text:
            items.append(text)
    return items


def normalize_mcp_server(server, index=0, used_ids=None):
    used_ids = used_ids if used_ids is not None else set()
    if not isinstance(server, dict):
        return None
    source = dict(server or {})
    transport = normalize_mcp_transport(source.get("transport", source.get("type")))
    name = str(source.get("name") or f"MCP Server {index + 1}").strip() or f"MCP Server {index + 1}"
    server_id = str(source.get("id") or "").strip() or f"mcp-{_slug_config_value(name)}"
    base_id = server_id
    suffix = 2
    while server_id in used_ids:
        server_id = f"{base_id}-{suffix}"
        suffix += 1
    used_ids.add(server_id)
    timeout_seconds = int(
        source.get("timeout_seconds")
        or (int(source.get("startup_timeout_ms")) / 1000 if source.get("startup_timeout_ms") else 0)
        or DEFAULT_MCP_TIMEOUT_SECONDS
    )
    timeout_seconds = max(5, min(300, timeout_seconds))
    args = normalize_mcp_string_list(source.get("args"))
    env = normalize_mcp_env_or_headers(source.get("env"))
    headers = normalize_mcp_env_or_headers(source.get("headers"))
    cwd = str(source.get("cwd") or "").strip()
    if cwd:
        cwd = os.path.normpath(os.path.abspath(os.path.expanduser(cwd)))
    return {
        "id": server_id,
        "name": name,
        "enabled": bool(source.get("enabled", True)),
        "transport": transport,
        "type": transport,
        "timeout_seconds": timeout_seconds,
        "startup_timeout_ms": timeout_seconds * 1000,
        "command": str(source.get("command") or "").strip(),
        "args": args,
        "cwd": cwd,
        "env": env,
        "url": str(source.get("url") or "").strip(),
        "headers": headers,
    }


def normalize_mcp_servers(value):
    if not isinstance(value, list):
        return []
    normalized = []
    used_ids = set()
    for index, item in enumerate(value):
        entry = normalize_mcp_server(item, index=index, used_ids=used_ids)
        if entry:
            normalized.append(entry)
    return normalized


def _looks_like_single_mcp_server(value):
    if not isinstance(value, dict):
        return False
    keys = {str(key or "").strip() for key in value.keys()}
    hint_keys = {
        "id",
        "name",
        "enabled",
        "transport",
        "type",
        "timeout_seconds",
        "startup_timeout_ms",
        "command",
        "args",
        "cwd",
        "env",
        "url",
        "headers",
    }
    return bool(keys & hint_keys)


def _coerce_named_mcp_servers(value, container_name):
    if not isinstance(value, dict):
        raise ValueError(f"`{container_name}` must be a JSON object.")
    servers = []
    for raw_name, raw_server in value.items():
        name = str(raw_name or "").strip()
        if not name:
            continue
        if not isinstance(raw_server, dict):
            raise ValueError(f"`{container_name}.{name}` must be a JSON object.")
        server = dict(raw_server)
        if not str(server.get("id") or "").strip():
            server["id"] = name
        if not str(server.get("name") or "").strip():
            server["name"] = name
        servers.append(server)
    return servers


def parse_mcp_servers_json(value):
    payload = value
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid MCP JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
            ) from exc

    if isinstance(payload, list):
        return payload

    if _looks_like_single_mcp_server(payload):
        return [payload]

    if not isinstance(payload, dict):
        raise ValueError("MCP JSON must be an object, an array, or a single server object.")

    if "mcpServers" in payload:
        return _coerce_named_mcp_servers(payload.get("mcpServers"), "mcpServers")

    if "mcp_servers" in payload:
        nested = payload.get("mcp_servers")
        if isinstance(nested, list):
            return nested
        if _looks_like_single_mcp_server(nested):
            return [nested]
        return _coerce_named_mcp_servers(nested, "mcp_servers")

    raise ValueError("MCP JSON must contain `mcpServers`, `mcp_servers`, or a server list.")


class ConfigManager:
    def __init__(self):
        self.config_file = "config.json"
        
        # Use centralized data directory logic
        self.data_dir = get_app_data_dir()
        self.config_path = os.path.join(self.data_dir, self.config_file)
        self._loaded_config_keys = set()
        self._save_batch_depth = 0
        self._save_pending = False
        
        # Migration: Check if config exists in old location (base_dir)
        base_dir = get_base_dir()
        old_config_path = os.path.join(base_dir, self.config_file)
        
        # If old config exists and new config doesn't, migrate it.
        # Check inequality to avoid copy error if paths are same (e.g. portable mode setup)
        if os.path.abspath(old_config_path) != os.path.abspath(self.config_path):
             if os.path.exists(old_config_path) and not os.path.exists(self.config_path):
                print(f"[Config] Migrating config from {old_config_path} to {self.config_path}")
                try:
                    shutil.copy2(old_config_path, self.config_path)
                except Exception as e:
                    print(f"[Config] Migration failed: {e}")

        self.config = {
            "api_key": "",
            "base_url": DEFAULT_DEEPSEEK_BASE_URL,
            "model_name": DEFAULT_DEEPSEEK_MODEL,
            "llm_provider": "openai",
            "selected_model_id": "openai-default",
            "model_channels": self._default_model_channels(),
            "model_provider_configs": self._default_model_provider_configs(),
            "deepseek_thinking_enabled": DEFAULT_DEEPSEEK_THINKING_ENABLED,
            "deepseek_reasoning_effort": DEFAULT_DEEPSEEK_REASONING_EFFORT,
            "deepseek_v4_context_window_tokens": 1000000,
            "context_budget_ratio": 0.8,
            "context_compression_recent_keep_turns": 40,
            "disabled_skills": [],
            "enabled_skills": [],
            "agent_profiles": self._default_agent_profiles(),
            "automation_tasks": [],
            "automation_run_history": [],
            "mcp_servers": [],
            "skill_configs": {},
            "projects": [],
            "god_mode": False,
            "download_sources": default_download_sources(),
            "default_workspace": "",
            "im_gateway": {
                "enabled_providers": [],
                "providers": {
                    "feishu": {"enabled": False, "long_connection": True},
                    "dingtalk": {"enabled": False},
                    "wecom": {"enabled": False},
                },
            },
        }
        self.load_config()
        self._apply_migrations()
        self.config["download_sources"] = normalize_download_sources(self.config.get("download_sources"))

    def get_god_mode(self):
        return self.config.get("god_mode", False)

    def set_god_mode(self, enabled: bool):
        self._set_config_value("god_mode", bool(enabled))

    def get_chat_history_dir(self):
        default_dir = os.path.join(self.data_dir, 'chat_history')
        return self.config.get("chat_history_dir", default_dir)

    def set_chat_history_dir(self, path):
        if self.get_chat_history_dir() == path:
            return False
        self.config["chat_history_dir"] = path
        self.save_config()
        return True

    @contextmanager
    def batch_save(self):
        self._save_batch_depth += 1
        try:
            yield self
        finally:
            self._save_batch_depth = max(0, self._save_batch_depth - 1)
            if self._save_batch_depth == 0 and self._save_pending:
                self._save_pending = False
                self._write_config()

    def load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._loaded_config_keys = set(data.keys())
                    self.config.update(data)
            except Exception as e:
                print(f"Error loading config: {e}")

    def _apply_migrations(self):
        updated = False
        if should_migrate_legacy_model(self.config.get("model_name")):
            self.config["model_name"] = DEFAULT_DEEPSEEK_MODEL
            updated = True
        raw_channels = (
            self.config.get("model_channels")
            if "model_channels" in self._loaded_config_keys
            else None
        )
        raw_provider_configs = (
            self.config.get("model_provider_configs")
            if "model_provider_configs" in self._loaded_config_keys
            else None
        )
        normalized_channels = self._normalize_model_channels(
            raw_channels,
            raw_provider_configs,
        )
        if normalized_channels != self.config.get("model_channels"):
            self.config["model_channels"] = normalized_channels
            updated = True
        legacy_provider_configs = self._provider_configs_from_channels(normalized_channels)
        if legacy_provider_configs != self.config.get("model_provider_configs"):
            self.config["model_provider_configs"] = legacy_provider_configs
            updated = True
        if not self._get_profile_from_configs(
            normalized_channels,
            self.config.get("selected_model_id"),
        ):
            self.config["selected_model_id"] = self._first_model_id(normalized_channels)
            updated = True
        normalized_agent_profiles = self._normalize_agent_profiles(
            self.config.get("agent_profiles")
        )
        if normalized_agent_profiles != self.config.get("agent_profiles"):
            self.config["agent_profiles"] = normalized_agent_profiles
            updated = True
        if self.config.get("sop_templates"):
            self.config["sop_templates"] = []
            updated = True
        normalized_automation_tasks = self._normalize_automation_tasks(
            self.config.get("automation_tasks"),
            valid_agent_profile_ids=[item.get("id") for item in normalized_agent_profiles],
        )
        if normalized_automation_tasks != self.config.get("automation_tasks"):
            self.config["automation_tasks"] = normalized_automation_tasks
            updated = True
        normalized_automation_history = self._normalize_automation_history(
            self.config.get("automation_run_history")
        )
        if normalized_automation_history != self.config.get("automation_run_history"):
            self.config["automation_run_history"] = normalized_automation_history
            updated = True
        normalized_mcp_servers = self._normalize_mcp_servers(self.config.get("mcp_servers"))
        if normalized_mcp_servers != self.config.get("mcp_servers"):
            self.config["mcp_servers"] = normalized_mcp_servers
            updated = True
        normalized_skill_configs = self._normalize_skill_configs(self.config.get("skill_configs"))
        if normalized_skill_configs != self.config.get("skill_configs"):
            self.config["skill_configs"] = normalized_skill_configs
            updated = True
        normalized_projects = self._normalize_projects(self.config.get("projects"))
        if normalized_projects != self.config.get("projects"):
            self.config["projects"] = normalized_projects
            updated = True
        updated = self._sync_legacy_model_fields(save=False) or updated
        if updated:
            self.save_config()

    def _default_model_provider_configs(self):
        return {
            "openai": {
                "display_name": "OpenAI 兼容服务",
                "api_key": "",
                "base_url": DEFAULT_DEEPSEEK_BASE_URL,
                "models": [
                    {
                        "id": "openai-default",
                        "display_name": "DeepSeek V4 Pro",
                        "model_name": DEFAULT_DEEPSEEK_MODEL,
                        "deepseek_thinking_enabled": DEFAULT_DEEPSEEK_THINKING_ENABLED,
                        "deepseek_reasoning_effort": DEFAULT_DEEPSEEK_REASONING_EFFORT,
                    }
                ],
            },
            "anthropic": {
                "display_name": "Anthropic",
                "api_key": "",
                "base_url": DEFAULT_ANTHROPIC_BASE_URL,
                "models": [
                    {
                        "id": "anthropic-default",
                        "display_name": "Claude Sonnet",
                        "model_name": DEFAULT_ANTHROPIC_MODEL,
                    }
                ],
            },
        }

    def _default_model_channels(self):
        return [
            {
                "channel_id": "openai-default-channel",
                "display_name": "OpenAI 兼容服务",
                "provider_type": "openai",
                "api_key": "",
                "base_url": DEFAULT_DEEPSEEK_BASE_URL,
                "models": [
                    {
                        "id": "openai-default",
                        "display_name": "DeepSeek V4 Pro",
                        "model_name": DEFAULT_DEEPSEEK_MODEL,
                        "deepseek_thinking_enabled": DEFAULT_DEEPSEEK_THINKING_ENABLED,
                        "deepseek_reasoning_effort": DEFAULT_DEEPSEEK_REASONING_EFFORT,
                    }
                ],
            },
            {
                "channel_id": "anthropic-default-channel",
                "display_name": "Anthropic",
                "provider_type": "anthropic",
                "api_key": "",
                "base_url": DEFAULT_ANTHROPIC_BASE_URL,
                "models": [
                    {
                        "id": "anthropic-default",
                        "display_name": "Claude Sonnet",
                        "model_name": DEFAULT_ANTHROPIC_MODEL,
                    }
                ],
            },
        ]

    def _default_agent_profiles(self):
        return []

    def _slug(self, value):
        return _slug_config_value(value)

    def _make_model_id(self, provider_id, model_name):
        return f"{provider_id}-{self._slug(model_name)}"

    def _normalize_provider_type(self, value):
        provider_type = str(value or "openai").strip().lower()
        return "anthropic" if provider_type == "anthropic" else "openai"

    def _normalize_agent_profile(self, profile, index=0, used_ids=None):
        used_ids = used_ids if used_ids is not None else set()
        source = dict(profile or {})
        name = str(source.get("name") or source.get("display_name") or "").strip()
        if not name:
            return None
        profile_id = str(source.get("id") or "").strip() or f"agent-{self._slug(name)}"
        base_profile_id = profile_id
        suffix = 2
        while profile_id in used_ids:
            profile_id = f"{base_profile_id}-{suffix}"
            suffix += 1
        used_ids.add(profile_id)
        skill_names = []
        seen_skills = set()
        for item in source.get("skill_names") or source.get("selected_skill_names") or []:
            text = str(item or "").strip()
            if not text or text in seen_skills:
                continue
            seen_skills.add(text)
            skill_names.append(text)
        now = int(time.time())
        created_at = int(source.get("created_at") or now)
        updated_at = int(source.get("updated_at") or created_at or now)
        return {
            "id": profile_id,
            "name": name,
            "description": str(source.get("description") or "").strip(),
            "system_prompt": str(source.get("system_prompt") or source.get("prompt") or "").strip(),
            "skill_names": skill_names,
            "enabled": bool(source.get("enabled", True)),
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _normalize_agent_profiles(self, value):
        profiles = value if isinstance(value, list) else []
        normalized = []
        used_ids = set()
        for index, profile in enumerate(profiles):
            entry = self._normalize_agent_profile(profile, index=index, used_ids=used_ids)
            if entry:
                normalized.append(entry)
        return normalized

    def _normalize_automation_tasks(self, value, valid_agent_profile_ids=None):
        return normalize_automation_tasks(value, valid_agent_profile_ids=valid_agent_profile_ids)

    def _normalize_automation_history(self, value):
        return normalize_automation_history(value)

    def _normalize_mcp_env_or_headers(self, value):
        return normalize_mcp_env_or_headers(value)

    def _normalize_mcp_string_list(self, value):
        return normalize_mcp_string_list(value)

    def _normalize_mcp_server(self, server, index=0, used_ids=None):
        return normalize_mcp_server(server, index=index, used_ids=used_ids)

    def _normalize_mcp_servers(self, value):
        return normalize_mcp_servers(value)

    def _normalize_skill_config_values(self, value):
        if not isinstance(value, dict):
            return {}
        normalized = {}
        for key, item in value.items():
            text_key = str(key or "").strip()
            if not text_key:
                continue
            normalized[text_key] = str(item if item is not None else "")
        return normalized

    def _normalize_skill_configs(self, value):
        if not isinstance(value, dict):
            return {}
        normalized = {}
        for skill_name, item in value.items():
            name = str(skill_name or "").strip()
            if not name:
                continue
            values = self._normalize_skill_config_values(item)
            if values:
                normalized[name] = values
        return normalized

    def _normalize_project_path(self, path):
        text = str(path or "").strip()
        if not text:
            return ""
        return os.path.normpath(os.path.abspath(os.path.expanduser(text)))

    def _project_key(self, path):
        normalized = self._normalize_project_path(path)
        return os.path.normcase(normalized)

    def _normalize_project(self, project, used_paths=None):
        used_paths = used_paths if used_paths is not None else set()
        if not isinstance(project, dict):
            return None
        source = dict(project or {})
        path = self._normalize_project_path(source.get("path"))
        if not path:
            return None
        key = self._project_key(path)
        if key in used_paths:
            return None
        used_paths.add(key)
        name = str(source.get("name") or "").strip()
        if not name:
            name = os.path.basename(path.rstrip(os.sep)) or path
        return {
            "path": path,
            "name": name,
            "pinned": bool(source.get("pinned", False)),
            "hidden": bool(source.get("hidden", False)),
            "created_at": int(source.get("created_at") or time.time()),
            "updated_at": int(source.get("updated_at") or time.time()),
        }

    def _normalize_projects(self, value):
        if not isinstance(value, list):
            return []
        projects = []
        used_paths = set()
        for item in value:
            normalized = self._normalize_project(item, used_paths=used_paths)
            if normalized:
                projects.append(normalized)
        return projects

    def _normalize_model_entry(self, provider_id, model, index=0, id_prefix=None):
        source = dict(model or {})
        model_name = str(source.get("model_name") or source.get("name") or "").strip()
        if should_migrate_legacy_model(model_name):
            model_name = DEFAULT_DEEPSEEK_MODEL if provider_id == "openai" else DEFAULT_ANTHROPIC_MODEL
        display_name = str(source.get("display_name") or source.get("label") or model_name).strip()
        model_id = str(source.get("id") or "").strip() or self._make_model_id(id_prefix or provider_id, model_name)
        if not model_id:
            model_id = f"{id_prefix or provider_id}-model-{index + 1}"
        entry = {
            "id": model_id,
            "display_name": display_name or model_name,
            "model_name": model_name,
            "supports_vision": bool(source.get("supports_vision", False)),
        }
        if provider_id == "openai":
            thinking_enabled = bool(
                source.get(
                    "deepseek_thinking_enabled",
                    source.get("thinking_enabled", DEFAULT_DEEPSEEK_THINKING_ENABLED),
                )
            )
            legacy_effort = str(
                source.get(
                    "deepseek_reasoning_effort",
                    source.get("reasoning_effort", DEFAULT_DEEPSEEK_REASONING_EFFORT),
                )
                or DEFAULT_DEEPSEEK_REASONING_EFFORT
            )
            raw_efforts = source.get("reasoning_efforts")
            if isinstance(raw_efforts, list):
                reasoning_efforts = normalize_reasoning_efforts(raw_efforts)
            elif "deepseek" in model_name.lower() and thinking_enabled:
                reasoning_efforts = ["high", "max"]
            else:
                reasoning_efforts = []
            reasoning_effort = normalize_reasoning_effort(
                source.get("reasoning_effort", legacy_effort),
                reasoning_efforts,
            )
            if reasoning_efforts and not reasoning_effort:
                reasoning_effort = "medium" if "medium" in reasoning_efforts else reasoning_efforts[0]
            entry["deepseek_thinking_enabled"] = thinking_enabled
            entry["deepseek_reasoning_effort"] = reasoning_effort or legacy_effort
            entry["reasoning_efforts"] = reasoning_efforts
            entry["reasoning_effort"] = reasoning_effort
        return entry

    def _normalize_channel_config(self, channel, index=0, used_channel_ids=None, used_model_ids=None):
        used_channel_ids = used_channel_ids if used_channel_ids is not None else set()
        used_model_ids = used_model_ids if used_model_ids is not None else set()
        source = dict(channel or {})
        provider_type = self._normalize_provider_type(
            source.get("provider_type", source.get("provider"))
        )
        default_name = "Anthropic" if provider_type == "anthropic" else "OpenAI 兼容服务"
        display_name = str(source.get("display_name") or source.get("label") or default_name).strip()
        channel_id = str(source.get("channel_id") or source.get("id") or "").strip()
        if not channel_id:
            channel_id = f"{provider_type}-{self._slug(display_name)}"
        base_channel_id = channel_id
        suffix = 2
        while channel_id in used_channel_ids:
            channel_id = f"{base_channel_id}-{suffix}"
            suffix += 1
        used_channel_ids.add(channel_id)

        models = source.get("models") if isinstance(source.get("models"), list) else []
        normalized_models = []
        for model_index, model in enumerate(models):
            entry = self._normalize_model_entry(
                provider_type,
                model,
                model_index,
                id_prefix=channel_id,
            )
            base_id = entry["id"]
            suffix = 2
            while entry["id"] in used_model_ids:
                entry["id"] = f"{base_id}-{suffix}"
                suffix += 1
            used_model_ids.add(entry["id"])
            normalized_models.append(entry)

        return {
            "channel_id": channel_id,
            "display_name": display_name or default_name,
            "provider_type": provider_type,
            "api_key": str(source.get("api_key") if source.get("api_key") is not None else ""),
            "base_url": str(
                source.get("base_url")
                or (DEFAULT_ANTHROPIC_BASE_URL if provider_type == "anthropic" else DEFAULT_DEEPSEEK_BASE_URL)
                or ""
            ).strip(),
            "models": normalized_models,
        }

    def _normalize_provider_config(self, provider_id, config):
        defaults = self._default_model_provider_configs().get(provider_id, {})
        source = dict(config or {})
        models = source.get("models") if isinstance(source.get("models"), list) else []
        normalized_models = []
        used_ids = set()
        for index, model in enumerate(models):
            entry = self._normalize_model_entry(provider_id, model, index)
            base_id = entry["id"]
            suffix = 2
            while entry["id"] in used_ids:
                entry["id"] = f"{base_id}-{suffix}"
                suffix += 1
            used_ids.add(entry["id"])
            normalized_models.append(entry)
        if not normalized_models:
            normalized_models = [
                self._normalize_model_entry(provider_id, defaults.get("models", [{}])[0], 0)
            ]
        return {
            "display_name": str(source.get("display_name") or defaults.get("display_name") or provider_id).strip(),
            "api_key": str(source.get("api_key") if source.get("api_key") is not None else defaults.get("api_key", "")),
            "base_url": str(source.get("base_url") or defaults.get("base_url") or "").strip(),
            "models": normalized_models,
        }

    def _normalize_model_provider_configs(self, value):
        defaults = self._default_model_provider_configs()
        source = value if isinstance(value, dict) else {}
        provider_configs = {}
        for provider_id in ("openai", "anthropic"):
            provider_configs[provider_id] = self._normalize_provider_config(
                provider_id,
                source.get(provider_id) if isinstance(source.get(provider_id), dict) else defaults.get(provider_id),
            )

        legacy_provider = str(self.config.get("llm_provider") or "openai").strip().lower()
        if legacy_provider not in provider_configs:
            legacy_provider = "openai"
        if not isinstance(value, dict) or not value:
            legacy_model = self.config.get("model_name", DEFAULT_DEEPSEEK_MODEL)
            if should_migrate_legacy_model(legacy_model):
                legacy_model = DEFAULT_DEEPSEEK_MODEL
            provider_configs[legacy_provider]["api_key"] = str(self.config.get("api_key", "") or "")
            provider_configs[legacy_provider]["base_url"] = str(
                self.config.get(
                    "base_url",
                    DEFAULT_DEEPSEEK_BASE_URL if legacy_provider == "openai" else DEFAULT_ANTHROPIC_BASE_URL,
                )
                or ""
            )
            provider_configs[legacy_provider]["models"] = [
                self._normalize_model_entry(
                    legacy_provider,
                    {
                        "id": f"{legacy_provider}-default",
                        "display_name": legacy_model,
                        "model_name": legacy_model,
                        "deepseek_thinking_enabled": self.config.get(
                            "deepseek_thinking_enabled",
                            DEFAULT_DEEPSEEK_THINKING_ENABLED,
                        ),
                        "deepseek_reasoning_effort": self.config.get(
                            "deepseek_reasoning_effort",
                            DEFAULT_DEEPSEEK_REASONING_EFFORT,
                        ),
                    },
                    0,
                )
            ]
        return provider_configs

    def _channels_from_provider_configs(self, provider_configs):
        defaults = self._default_model_provider_configs()
        source = provider_configs if isinstance(provider_configs, dict) else {}
        channels = []
        for provider_id in ("openai", "anthropic"):
            raw_config = source.get(provider_id) if isinstance(source.get(provider_id), dict) else defaults.get(provider_id)
            provider_config = self._normalize_provider_config(provider_id, raw_config)
            channels.append(
                {
                    "channel_id": f"{provider_id}-default-channel",
                    "display_name": provider_config.get("display_name") or provider_id,
                    "provider_type": provider_id,
                    "api_key": provider_config.get("api_key", ""),
                    "base_url": provider_config.get("base_url", ""),
                    "models": provider_config.get("models", []),
                }
            )
        return channels

    def _normalize_model_channels(self, value, legacy_provider_configs=None):
        if isinstance(value, list) and value:
            raw_channels = value
        elif isinstance(legacy_provider_configs, dict) and legacy_provider_configs:
            raw_channels = self._channels_from_provider_configs(legacy_provider_configs)
        else:
            raw_channels = json.loads(json.dumps(self._default_model_channels(), ensure_ascii=False))
            legacy_provider = self._normalize_provider_type(self.config.get("llm_provider", "openai"))
            legacy_model = self.config.get("model_name", DEFAULT_DEEPSEEK_MODEL)
            if should_migrate_legacy_model(legacy_model):
                legacy_model = DEFAULT_DEEPSEEK_MODEL
            for channel in raw_channels:
                if channel.get("provider_type") != legacy_provider:
                    continue
                channel["api_key"] = str(self.config.get("api_key", "") or "")
                channel["base_url"] = str(
                    self.config.get(
                        "base_url",
                        DEFAULT_DEEPSEEK_BASE_URL if legacy_provider == "openai" else DEFAULT_ANTHROPIC_BASE_URL,
                    )
                    or ""
                )
                channel["models"] = [
                    {
                        "id": f"{legacy_provider}-default",
                        "display_name": legacy_model,
                        "model_name": legacy_model,
                        "deepseek_thinking_enabled": self.config.get(
                            "deepseek_thinking_enabled",
                            DEFAULT_DEEPSEEK_THINKING_ENABLED,
                        ),
                        "deepseek_reasoning_effort": self.config.get(
                            "deepseek_reasoning_effort",
                            DEFAULT_DEEPSEEK_REASONING_EFFORT,
                        ),
                    }
                ]
        normalized_channels = []
        used_channel_ids = set()
        used_model_ids = set()
        for index, channel in enumerate(raw_channels):
            normalized_channels.append(
                self._normalize_channel_config(
                    channel,
                    index,
                    used_channel_ids=used_channel_ids,
                    used_model_ids=used_model_ids,
                )
            )
        if not normalized_channels:
            return self._normalize_model_channels(self._default_model_channels())
        if not any(channel.get("models") for channel in normalized_channels):
            default_channel = self._normalize_channel_config(
                self._default_model_channels()[0],
                0,
                used_channel_ids=used_channel_ids,
                used_model_ids=used_model_ids,
            )
            normalized_channels[0]["models"] = default_channel.get("models", [])
        return normalized_channels

    def _provider_configs_from_channels(self, channels):
        provider_configs = {}
        for provider_id in ("openai", "anthropic"):
            matching = [
                channel
                for channel in (channels or [])
                if channel.get("provider_type") == provider_id
            ]
            if matching:
                channel = matching[0]
                models = []
                for item in matching:
                    models.extend(item.get("models") or [])
                provider_configs[provider_id] = {
                    "display_name": channel.get("display_name") or provider_id,
                    "api_key": channel.get("api_key", ""),
                    "base_url": channel.get("base_url", ""),
                    "models": models,
                }
            else:
                provider_configs[provider_id] = self._normalize_provider_config(
                    provider_id,
                    self._default_model_provider_configs().get(provider_id),
                )
        return provider_configs

    def _first_model_id(self, configs=None):
        configs = configs or self.config.get("model_channels") or []
        if isinstance(configs, dict):
            for provider_id in ("openai", "anthropic"):
                models = ((configs.get(provider_id) or {}).get("models") or [])
                if models:
                    return models[0].get("id")
            return "openai-default"
        for channel in configs or []:
            for model in channel.get("models") or []:
                if model.get("id"):
                    return model.get("id")
        return "openai-default"

    def _get_profile_from_configs(self, configs, model_id=None):
        selected_id = str(model_id or "").strip()
        if isinstance(configs, dict):
            configs = self._normalize_model_channels(None, configs)
        for channel in configs or []:
            provider_type = channel.get("provider_type") or "openai"
            for model in channel.get("models") or []:
                if selected_id and model.get("id") != selected_id:
                    continue
                profile = dict(model)
                profile["provider"] = provider_type
                profile["provider_type"] = provider_type
                profile["provider_display_name"] = channel.get("display_name") or provider_type
                profile["channel_id"] = channel.get("channel_id") or ""
                profile["channel_display_name"] = channel.get("display_name") or provider_type
                profile["api_key"] = channel.get("api_key") or ""
                profile["base_url"] = channel.get("base_url") or ""
                return profile
        return None

    def get_model_channels(self):
        channels = self._normalize_model_channels(self.config.get("model_channels"))
        if channels != self.config.get("model_channels"):
            self.config["model_channels"] = channels
            self.config["model_provider_configs"] = self._provider_configs_from_channels(channels)
            self.save_config()
        return json.loads(json.dumps(channels, ensure_ascii=False))

    def set_model_channels(self, channels, selected_model_id=None):
        normalized_channels = self._normalize_model_channels(channels)
        self.config["model_channels"] = normalized_channels
        self.config["model_provider_configs"] = self._provider_configs_from_channels(normalized_channels)
        if selected_model_id is not None:
            self.config["selected_model_id"] = str(selected_model_id or "").strip()
        if not self._get_profile_from_configs(normalized_channels, self.config.get("selected_model_id")):
            self.config["selected_model_id"] = self._first_model_id(normalized_channels)
        self._sync_legacy_model_fields(
            save=False,
            profile=self._get_profile_from_configs(normalized_channels, self.config.get("selected_model_id")),
        )
        self.save_config()

    def get_model_provider_configs(self):
        configs = self._provider_configs_from_channels(self.get_model_channels())
        if configs != self.config.get("model_provider_configs"):
            self.config["model_provider_configs"] = configs
            self.save_config()
        return json.loads(json.dumps(configs, ensure_ascii=False))

    def set_model_provider_configs(self, provider_configs, selected_model_id=None):
        self.set_model_channels(
            self._channels_from_provider_configs(provider_configs),
            selected_model_id,
        )

    def iter_model_profiles(self):
        channels = self.get_model_channels()
        profiles = []
        for channel in channels:
            provider_type = channel.get("provider_type") or "openai"
            for model in channel.get("models") or []:
                profile = dict(model)
                profile["provider"] = provider_type
                profile["provider_type"] = provider_type
                profile["provider_display_name"] = channel.get("display_name") or provider_type
                profile["channel_id"] = channel.get("channel_id") or ""
                profile["channel_display_name"] = channel.get("display_name") or provider_type
                profile["api_key"] = channel.get("api_key") or ""
                profile["base_url"] = channel.get("base_url") or ""
                profiles.append(profile)
        return profiles

    def get_model_profile(self, model_id=None):
        configs = self.get_model_channels()
        selected_id = str(model_id or self.config.get("selected_model_id") or "").strip()
        profile = self._get_profile_from_configs(configs, selected_id)
        if profile:
            return profile
        return self._get_profile_from_configs(configs, self._first_model_id(configs))

    def get_selected_model_id(self):
        profile = self.get_model_profile(self.config.get("selected_model_id"))
        return profile.get("id") if profile else self._first_model_id()

    def set_selected_model_id(self, model_id):
        profile = self.get_model_profile(model_id)
        if not profile:
            return False
        self.config["selected_model_id"] = profile["id"]
        self._sync_legacy_model_fields(save=False)
        self.save_config()
        return True

    def set_model_reasoning_effort(self, model_id, reasoning_effort):
        selected_id = str(model_id or "").strip()
        channels = self.get_model_channels()
        for channel in channels:
            for model in channel.get("models") or []:
                if model.get("id") != selected_id:
                    continue
                efforts = normalize_reasoning_efforts(model.get("reasoning_efforts"))
                effort = normalize_reasoning_effort(reasoning_effort, efforts)
                if not effort:
                    return False
                model["reasoning_effort"] = effort
                model["deepseek_reasoning_effort"] = effort
                self.set_model_channels(channels, self.config.get("selected_model_id"))
                return True
        return False

    def get_model_label(self, model_id=None, include_provider=True):
        profile = self.get_model_profile(model_id)
        if not profile:
            return DEFAULT_DEEPSEEK_MODEL
        name = profile.get("display_name") or profile.get("model_name") or DEFAULT_DEEPSEEK_MODEL
        if not include_provider:
            return name
        provider = profile.get("channel_display_name") or profile.get("provider_display_name") or profile.get("provider") or ""
        return f"{provider} / {name}" if provider else name

    def _sync_legacy_model_fields(self, save=True, profile=None):
        profile = profile or self.get_model_profile(self.config.get("selected_model_id"))
        if not profile:
            return False
        updates = {
            "selected_model_id": profile.get("id"),
            "llm_provider": profile.get("provider_type") or profile.get("provider", "openai"),
            "api_key": profile.get("api_key", ""),
            "base_url": profile.get("base_url", ""),
            "model_name": profile.get("model_name", DEFAULT_DEEPSEEK_MODEL),
        }
        if (profile.get("provider_type") or profile.get("provider")) == "openai":
            updates["deepseek_thinking_enabled"] = profile.get(
                "deepseek_thinking_enabled",
                DEFAULT_DEEPSEEK_THINKING_ENABLED,
            )
            updates["deepseek_reasoning_effort"] = profile.get(
                "deepseek_reasoning_effort",
                DEFAULT_DEEPSEEK_REASONING_EFFORT,
            )
        changed = False
        for key, value in updates.items():
            if self.config.get(key) != value:
                self.config[key] = value
                changed = True
        if changed and save:
            self.save_config()
        return changed

    def get_agent_profiles(self):
        profiles = self._normalize_agent_profiles(self.config.get("agent_profiles"))
        if profiles != self.config.get("agent_profiles"):
            self.config["agent_profiles"] = profiles
            self.save_config()
        return json.loads(json.dumps(profiles, ensure_ascii=False))

    def set_agent_profiles(self, profiles):
        normalized = self._normalize_agent_profiles(profiles)
        if normalized == self.config.get("agent_profiles"):
            return
        self.config["agent_profiles"] = normalized
        self.save_config()

    def get_agent_profile(self, profile_id_or_name):
        identifier = str(profile_id_or_name or "").strip()
        if not identifier:
            return None
        profiles = self.get_agent_profiles()
        for profile in profiles:
            if profile.get("id") == identifier:
                return profile
        for profile in profiles:
            if profile.get("name") == identifier:
                return profile
        return None

    def get_automation_tasks(self):
        valid_agent_profile_ids = [item.get("id") for item in self.get_agent_profiles()]
        tasks = self._normalize_automation_tasks(
            self.config.get("automation_tasks"),
            valid_agent_profile_ids=valid_agent_profile_ids,
        )
        if tasks != self.config.get("automation_tasks"):
            self.config["automation_tasks"] = tasks
            self.save_config()
        return json.loads(json.dumps(tasks, ensure_ascii=False))

    def set_automation_tasks(self, tasks):
        valid_agent_profile_ids = [item.get("id") for item in self.get_agent_profiles()]
        normalized = self._normalize_automation_tasks(tasks, valid_agent_profile_ids=valid_agent_profile_ids)
        if normalized == self.config.get("automation_tasks"):
            return
        self.config["automation_tasks"] = normalized
        self.save_config()

    def get_automation_task(self, task_id_or_name):
        identifier = str(task_id_or_name or "").strip()
        if not identifier:
            return None
        tasks = self.get_automation_tasks()
        for task in tasks:
            if task.get("id") == identifier:
                return task
        for task in tasks:
            if task.get("name") == identifier:
                return task
        return None

    def get_automation_run_history(self):
        history = self._normalize_automation_history(self.config.get("automation_run_history"))
        if history != self.config.get("automation_run_history"):
            self.config["automation_run_history"] = history
            self.save_config()
        return json.loads(json.dumps(history, ensure_ascii=False))

    def set_automation_run_history(self, history):
        normalized = self._normalize_automation_history(history)
        if normalized == self.config.get("automation_run_history"):
            return
        self.config["automation_run_history"] = normalized
        self.save_config()

    def append_automation_run_history(self, record):
        history = self.get_automation_run_history()
        history.insert(0, record)
        normalized = self._normalize_automation_history(history)[:AUTOMATION_HISTORY_LIMIT]
        self.config["automation_run_history"] = normalized
        self.save_config()
        return normalized[0] if normalized else None

    def get_projects(self, include_hidden=True):
        projects = self._normalize_projects(self.config.get("projects"))
        if projects != self.config.get("projects"):
            self.config["projects"] = projects
            self.save_config()
        if not include_hidden:
            projects = [item for item in projects if not item.get("hidden")]
        return json.loads(json.dumps(projects, ensure_ascii=False))

    def set_projects(self, projects):
        normalized = self._normalize_projects(projects)
        if normalized == self.config.get("projects"):
            return
        self.config["projects"] = normalized
        self.save_config()

    def get_mcp_servers(self):
        servers = self._normalize_mcp_servers(self.config.get("mcp_servers"))
        if servers != self.config.get("mcp_servers"):
            self.config["mcp_servers"] = servers
            self.save_config()
        return json.loads(json.dumps(servers, ensure_ascii=False))

    def set_mcp_servers(self, servers):
        normalized = self._normalize_mcp_servers(servers)
        if normalized == self.config.get("mcp_servers"):
            return
        self.config["mcp_servers"] = normalized
        self.save_config()

    def get_skill_configs(self):
        configs = self._normalize_skill_configs(self.config.get("skill_configs"))
        if configs != self.config.get("skill_configs"):
            self.config["skill_configs"] = configs
            self.save_config()
        return json.loads(json.dumps(configs, ensure_ascii=False))

    def get_skill_config(self, skill_name):
        name = str(skill_name or "").strip()
        if not name:
            return {}
        return self.get_skill_configs().get(name, {})

    def set_skill_config(self, skill_name, values):
        name = str(skill_name or "").strip()
        if not name:
            return
        configs = self.get_skill_configs()
        normalized_values = self._normalize_skill_config_values(values)
        if normalized_values:
            configs[name] = normalized_values
        else:
            configs.pop(name, None)
        if configs == self.config.get("skill_configs"):
            return
        self.config["skill_configs"] = configs
        self.save_config()

    def upsert_project(self, path, name=None, pinned=None):
        normalized_path = self._normalize_project_path(path)
        if not normalized_path:
            return None
        now = int(time.time())
        projects = self.get_projects(include_hidden=True)
        key = self._project_key(normalized_path)
        updated = None
        for project in projects:
            if self._project_key(project.get("path")) != key:
                continue
            if name is not None:
                project["name"] = str(name or "").strip() or os.path.basename(normalized_path.rstrip(os.sep)) or normalized_path
            if pinned is not None:
                project["pinned"] = bool(pinned)
            project["hidden"] = False
            project["path"] = normalized_path
            project["updated_at"] = now
            updated = project
            break
        if updated is None:
            updated = {
                "path": normalized_path,
                "name": str(name or "").strip() or os.path.basename(normalized_path.rstrip(os.sep)) or normalized_path,
                "pinned": bool(pinned) if pinned is not None else False,
                "hidden": False,
                "created_at": now,
                "updated_at": now,
            }
            projects.append(updated)
        self.set_projects(projects)
        return json.loads(json.dumps(updated, ensure_ascii=False))

    def hide_project(self, path):
        normalized_path = self._normalize_project_path(path)
        if not normalized_path:
            return False
        projects = self.get_projects(include_hidden=True)
        key = self._project_key(normalized_path)
        changed = False
        for project in projects:
            if self._project_key(project.get("path")) != key:
                continue
            project["hidden"] = True
            project["updated_at"] = int(time.time())
            changed = True
            break
        if not changed:
            now = int(time.time())
            projects.append(
                {
                    "path": normalized_path,
                    "name": os.path.basename(normalized_path.rstrip(os.sep)) or normalized_path,
                    "pinned": False,
                    "hidden": True,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            changed = True
        self.set_projects(projects)
        return changed

    def restore_project(self, path):
        normalized_path = self._normalize_project_path(path)
        if not normalized_path:
            return False
        projects = self.get_projects(include_hidden=True)
        key = self._project_key(normalized_path)
        changed = False
        for project in projects:
            if self._project_key(project.get("path")) != key:
                continue
            project["hidden"] = False
            project["updated_at"] = int(time.time())
            changed = True
            break
        if changed:
            self.set_projects(projects)
            return True
        return bool(self.upsert_project(normalized_path))

    def save_config(self):
        if self._save_batch_depth > 0:
            self._save_pending = True
            return
        self._write_config()

    def _write_config(self):
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving config: {e}")

    def _set_config_value(self, key, value):
        if self.config.get(key) == value:
            return False
        self.config[key] = value
        self.save_config()
        return True

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        return self._set_config_value(key, value)

    def is_skill_enabled(self, skill_name, default_enabled=True):
        if skill_name in self.config.get("disabled_skills", []):
            return False
        if skill_name in self.config.get("enabled_skills", []):
            return True
        return bool(default_enabled)

    def set_skill_enabled(self, skill_name, enabled):
        disabled = set(self.config.get("disabled_skills", []))
        enabled_skills = set(self.config.get("enabled_skills", []))
        if enabled:
            disabled.discard(skill_name)
            enabled_skills.add(skill_name)
        else:
            disabled.add(skill_name)
            enabled_skills.discard(skill_name)
        self.config["disabled_skills"] = list(disabled)
        self.config["enabled_skills"] = list(enabled_skills)
        self.save_config()
