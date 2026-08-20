import json
import os
import sys
import shutil
import re
import uuid
import time
import copy
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
from .llm.providers import (
    API_PROTOCOL_CHAT_COMPLETIONS,
    normalize_openai_api_protocol,
)
from .llm.model_catalog import (
    DEEPSEEK_OFFICIAL_BASE_URL,
    get_recommended_model,
    is_deepseek_official_base_url,
)
from .favorites_manager import (
    FAVORITE_RUN_HISTORY_LIMIT,
    migrate_automation_task,
    normalize_favorite,
    normalize_favorite_run_history,
    normalize_favorites,
)
from .mcp_client import (
    DEFAULT_MCP_TIMEOUT_SECONDS,
    TRANSPORT_STDIO,
    clear_mcp_auth_cache,
    normalize_mcp_transport,
)
from .runtime_components import default_download_sources, normalize_download_sources
from .im_gateway_config import normalize_im_gateway_config

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"


def _recommended_deepseek_model():
    return get_recommended_model("openai", DEEPSEEK_OFFICIAL_BASE_URL)


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


def normalize_mcp_auth(value):
    if not isinstance(value, dict):
        return {}
    allowed = {
        "type",
        "skill_name",
        "base_url_field",
        "username_field",
        "password_field",
        "provider_field",
    }
    return {
        str(key): str(item if item is not None else "").strip()
        for key, item in value.items()
        if str(key or "").strip() in allowed and str(item if item is not None else "").strip()
    }


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
    auth = normalize_mcp_auth(source.get("auth"))
    cwd = str(source.get("cwd") or "").strip()
    if cwd:
        cwd = os.path.normpath(os.path.abspath(os.path.expanduser(cwd)))
    source_skill = str(source.get("source_skill") or "").strip()
    if not source_skill:
        source_skill = str(source.get("runtime_skill") or auth.get("skill_name") or "").strip()
    if not source_skill and server_id == "showdoc" and any("mcp-showdoc" in item.lower() for item in args):
        source_skill = "showdoc-mcp"
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
        "auth": auth,
        "runtime_skill": str(source.get("runtime_skill") or "").strip(),
        "source_skill": source_skill,
        "managed_by_skill": bool(source.get("managed_by_skill") or source_skill),
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
        "auth",
        "runtime_skill",
        "source_skill",
        "managed_by_skill",
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
            "selected_model_id": _recommended_deepseek_model(),
            "model_channels": self._default_model_channels(),
            "model_provider_configs": self._default_model_provider_configs(),
            "deepseek_thinking_enabled": DEFAULT_DEEPSEEK_THINKING_ENABLED,
            "deepseek_reasoning_effort": DEFAULT_DEEPSEEK_REASONING_EFFORT,
            "deepseek_v4_context_window_tokens": 1000000,
            "context_budget_ratio": 0.8,
            "context_compression_recent_keep_turns": 40,
            "disabled_skills": [],
            "enabled_skills": [],
            "skill_dependency_install_timeout_seconds": 300,
            "agent_profiles": self._default_agent_profiles(),
            "favorites": [],
            "favorite_run_history": [],
            "mcp_servers": [],
            "skill_configs": {},
            "projects": [],
            "god_mode": False,
            "download_sources": default_download_sources(),
            "default_workspace": "",
            "chat_workspace_root": os.path.join(self.data_dir, "conversation_workspaces"),
            "im_gateway": {
                "enabled_providers": [],
                "providers": {
                    "feishu": {"enabled": False, "long_connection": True},
                    "dingtalk": {"enabled": False},
                    "wecom": {"enabled": False},
                    "qq": {"enabled": False},
                    "wechat": {"enabled": False},
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

    def get_chat_workspace_root(self):
        default_dir = os.path.join(self.data_dir, "conversation_workspaces")
        path = str(self.config.get("chat_workspace_root") or default_dir).strip()
        return os.path.normpath(os.path.abspath(os.path.expanduser(path)))

    def set_chat_workspace_root(self, path):
        normalized = os.path.normpath(os.path.abspath(os.path.expanduser(str(path or "").strip())))
        if not str(path or "").strip():
            normalized = os.path.join(self.data_dir, "conversation_workspaces")
        if self.get_chat_workspace_root() == normalized:
            return False
        self.config["chat_workspace_root"] = normalized
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
        legacy_automation_present = any(
            key in self._loaded_config_keys
            for key in ("automation_tasks", "automation_run_history")
        )
        if "favorites" not in self._loaded_config_keys and legacy_automation_present:
            default_workspace = str(self.config.get("default_workspace") or "").strip()
            migration_workspace = default_workspace if os.path.isdir(default_workspace) else ""
            migrated_favorites = [
                migrate_automation_task(item, workspace_dir=migration_workspace)
                for item in self.config.get("automation_tasks") or []
            ]
            self.config["favorites"] = normalize_favorites(migrated_favorites)
            self.config["favorite_run_history"] = normalize_favorite_run_history(
                self.config.get("automation_run_history") or []
            )
            self.config.pop("automation_tasks", None)
            self.config.pop("automation_run_history", None)
            updated = True
        elif legacy_automation_present:
            self.config.pop("automation_tasks", None)
            self.config.pop("automation_run_history", None)
            updated = True
        normalized_favorites = self._normalize_favorites(self.config.get("favorites"))
        if normalized_favorites != self.config.get("favorites"):
            self.config["favorites"] = normalized_favorites
            updated = True
        normalized_favorite_history = self._normalize_favorite_run_history(
            self.config.get("favorite_run_history")
        )
        if normalized_favorite_history != self.config.get("favorite_run_history"):
            self.config["favorite_run_history"] = normalized_favorite_history
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
        normalized_im_gateway = normalize_im_gateway_config(self.config.get("im_gateway"))
        if normalized_im_gateway != self.config.get("im_gateway"):
            self.config["im_gateway"] = normalized_im_gateway
            updated = True
        updated = self._sync_legacy_model_fields(save=False) or updated
        if updated:
            self.save_config()

    def _default_deepseek_models(self):
        models = [
            {
                "id": "deepseek-v4-flash",
                "display_name": "deepseek-v4-flash",
                "model_name": "deepseek-v4-flash",
                "api_protocol": API_PROTOCOL_CHAT_COMPLETIONS,
                "deepseek_thinking_enabled": DEFAULT_DEEPSEEK_THINKING_ENABLED,
                "deepseek_reasoning_effort": DEFAULT_DEEPSEEK_REASONING_EFFORT,
            },
            {
                "id": "deepseek-v4-pro",
                "display_name": "deepseek-v4-pro",
                "model_name": DEFAULT_DEEPSEEK_MODEL,
                "api_protocol": API_PROTOCOL_CHAT_COMPLETIONS,
                "deepseek_thinking_enabled": DEFAULT_DEEPSEEK_THINKING_ENABLED,
                "deepseek_reasoning_effort": DEFAULT_DEEPSEEK_REASONING_EFFORT,
            },
        ]
        recommended = _recommended_deepseek_model()
        if recommended and all(model["model_name"] != recommended for model in models):
            models.append({
                "id": recommended,
                "display_name": recommended,
                "model_name": recommended,
                "supports_vision": False,
                "api_protocol": API_PROTOCOL_CHAT_COMPLETIONS,
                "deepseek_thinking_enabled": False,
                "deepseek_reasoning_effort": "",
                "reasoning_efforts": [],
                "reasoning_effort": "",
            })
        return sorted(
            models,
            key=lambda model: (
                str(model.get("model_name") or "") != recommended,
                str(model.get("model_name") or "").casefold(),
            ),
        )

    def _default_model_provider_configs(self):
        return {
            "openai": {
                "display_name": "deepseek官方",
                "api_key": "",
                "base_url": DEFAULT_DEEPSEEK_BASE_URL,
                "models": self._default_deepseek_models(),
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
                "channel_id": "deepseek-official-channel",
                "display_name": "deepseek官方",
                "provider_type": "openai",
                "api_key": "",
                "base_url": DEFAULT_DEEPSEEK_BASE_URL,
                "models": self._default_deepseek_models(),
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

    def _normalize_favorites(self, value):
        return normalize_favorites(value)

    def _normalize_favorite_run_history(self, value):
        return normalize_favorite_run_history(value)

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
            "archived": bool(source.get("archived", source.get("hidden", False))),
            "archived_at": int(source.get("archived_at") or 0),
            "hidden": bool(source.get("archived", source.get("hidden", False))),
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
            entry["api_protocol"] = normalize_openai_api_protocol(
                source.get("api_protocol", API_PROTOCOL_CHAT_COMPLETIONS)
            )
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

    def _channels_from_provider_configs(self, provider_configs, include_missing=True):
        defaults = self._default_model_provider_configs()
        source = provider_configs if isinstance(provider_configs, dict) else {}
        channels = []
        provider_ids = (
            ("openai", "anthropic")
            if include_missing
            else tuple(
                provider_id
                for provider_id in ("openai", "anthropic")
                if isinstance(source.get(provider_id), dict)
            )
        )
        for provider_id in provider_ids:
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
        if isinstance(value, list):
            raw_channels = value
        elif isinstance(legacy_provider_configs, dict) and legacy_provider_configs:
            raw_channels = self._channels_from_provider_configs(
                legacy_provider_configs,
                include_missing=False,
            )
        else:
            raw_channels = json.loads(json.dumps(self._default_model_channels(), ensure_ascii=False))
            legacy_model_keys = {
                "llm_provider",
                "api_key",
                "base_url",
                "model_name",
                "deepseek_thinking_enabled",
                "deepseek_reasoning_effort",
            }
            if legacy_model_keys.intersection(getattr(self, "_loaded_config_keys", set())):
                legacy_provider = self._normalize_provider_type(self.config.get("llm_provider", "openai"))
                legacy_model = self.config.get("model_name", DEFAULT_DEEPSEEK_MODEL)
                if should_migrate_legacy_model(legacy_model):
                    legacy_model = DEFAULT_DEEPSEEK_MODEL
                matching_channel = next(
                    (
                        channel
                        for channel in raw_channels
                        if channel.get("provider_type") == legacy_provider
                    ),
                    None,
                )
                if matching_channel is None:
                    matching_channel = {
                        "channel_id": f"{legacy_provider}-default-channel",
                        "display_name": (
                            "Anthropic" if legacy_provider == "anthropic" else "OpenAI 兼容服务"
                        ),
                        "provider_type": legacy_provider,
                        "api_key": "",
                        "base_url": (
                            DEFAULT_ANTHROPIC_BASE_URL
                            if legacy_provider == "anthropic"
                            else DEFAULT_DEEPSEEK_BASE_URL
                        ),
                        "models": [],
                    }
                    raw_channels = [matching_channel]
                else:
                    matching_channel["channel_id"] = f"{legacy_provider}-default-channel"
                    matching_channel["display_name"] = (
                        "Anthropic" if legacy_provider == "anthropic" else "OpenAI 兼容服务"
                    )
                matching_channel["api_key"] = str(self.config.get("api_key", "") or "")
                matching_channel["base_url"] = str(
                    self.config.get(
                        "base_url",
                        DEFAULT_DEEPSEEK_BASE_URL if legacy_provider == "openai" else DEFAULT_ANTHROPIC_BASE_URL,
                    )
                    or ""
                )
                matching_channel["models"] = [
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
        return ""

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

    def has_usable_model_profile(self):
        return any(
            str(profile.get("model_name") or "").strip()
            and str(profile.get("api_key") or "").strip()
            for profile in self.iter_model_profiles()
        )

    def apply_deepseek_quickstart(self, api_key, discovered_models):
        """Atomically configure the official DeepSeek channel after validation."""

        secret = str(api_key or "").strip()
        if not secret:
            raise ValueError("请填写 DeepSeek API Key。")
        discovered_ids = []
        seen = set()
        for item in discovered_models or []:
            model_name = str(item.get("id") if isinstance(item, dict) else item or "").strip()
            if not model_name or model_name in seen:
                continue
            seen.add(model_name)
            discovered_ids.append(model_name)
        recommended = _recommended_deepseek_model()
        if not recommended:
            raise ValueError("产品推荐模型尚未配置。")
        if recommended not in seen:
            available = "、".join(discovered_ids[:8]) or "无"
            raise ValueError(f"接口未返回推荐模型 {recommended}。当前可用模型：{available}")

        channels = self.get_model_channels()
        snapshot = copy.deepcopy(self.config)
        try:
            official = next(
                (
                    channel for channel in channels
                    if str(channel.get("provider_type") or "openai").lower() == "openai"
                    and is_deepseek_official_base_url(channel.get("base_url"))
                ),
                None,
            )
            if official is None:
                official = copy.deepcopy(self._default_model_channels()[0])
                channels.insert(0, official)
            normalized_default = self._normalize_model_channels(
                [copy.deepcopy(self._default_model_channels()[0])]
            )[0]
            pristine_product_default = official == normalized_default
            official["provider_type"] = "openai"
            official["base_url"] = DEEPSEEK_OFFICIAL_BASE_URL
            official["api_key"] = secret
            retained_models = [] if pristine_product_default else list(official.get("models") or [])
            existing_by_name = {
                str(model.get("model_name") or "").strip(): model
                for model in retained_models
                if str(model.get("model_name") or "").strip()
            }
            default_by_name = {
                str(model.get("model_name") or "").strip(): model
                for model in self._default_model_channels()[0].get("models") or []
            }
            merged_models = list(retained_models)
            for model_name in discovered_ids:
                if model_name in existing_by_name:
                    continue
                template = copy.deepcopy(default_by_name.get(model_name) or {
                    "id": f"{official.get('channel_id') or 'deepseek-official'}-{_slug_config_value(model_name)}",
                    "display_name": model_name,
                    "model_name": model_name,
                    "supports_vision": False,
                    "api_protocol": API_PROTOCOL_CHAT_COMPLETIONS,
                    "deepseek_thinking_enabled": False,
                    "deepseek_reasoning_effort": "",
                    "reasoning_efforts": [],
                    "reasoning_effort": "",
                })
                merged_models.append(template)
            official["models"] = merged_models

            normalized = self._normalize_model_channels(channels)
            selected_profile = None
            for channel in normalized:
                if not is_deepseek_official_base_url(channel.get("base_url")):
                    continue
                selected_profile = next(
                    (
                        dict(model) for model in channel.get("models") or []
                        if str(model.get("model_name") or "").strip() == recommended
                    ),
                    None,
                )
                if selected_profile:
                    selected_profile.update({
                        "provider": "openai",
                        "provider_type": "openai",
                        "provider_display_name": channel.get("display_name") or "deepseek官方",
                        "channel_id": channel.get("channel_id") or "",
                        "channel_display_name": channel.get("display_name") or "deepseek官方",
                        "api_key": secret,
                        "base_url": channel.get("base_url") or DEEPSEEK_OFFICIAL_BASE_URL,
                    })
                    break
            if not selected_profile:
                raise ValueError(f"无法保存推荐模型 {recommended}。")
            self.config["model_channels"] = normalized
            self.config["model_provider_configs"] = self._provider_configs_from_channels(normalized)
            self.config["selected_model_id"] = selected_profile["id"]
            self._sync_legacy_model_fields(save=False, profile=selected_profile)
            self._write_config_or_raise()
            return copy.deepcopy(selected_profile)
        except Exception:
            self.config = snapshot
            raise

    def get_model_profile(self, model_id=None):
        configs = self.get_model_channels()
        selected_id = str(model_id or self.config.get("selected_model_id") or "").strip()
        profile = self._get_profile_from_configs(configs, selected_id)
        if profile:
            return profile
        return self._get_profile_from_configs(configs, self._first_model_id(configs))

    def get_selected_model_id(self):
        profile = self.get_model_profile(self.config.get("selected_model_id"))
        return profile.get("id") if profile else ""

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

    def get_favorites(self):
        favorites = self._normalize_favorites(self.config.get("favorites"))
        if favorites != self.config.get("favorites"):
            self.config["favorites"] = favorites
            self.save_config()
        return json.loads(json.dumps(favorites, ensure_ascii=False))

    def set_favorites(self, favorites):
        normalized = self._normalize_favorites(favorites)
        if normalized == self.config.get("favorites"):
            return
        self.config["favorites"] = normalized
        self.save_config()

    def get_favorite(self, favorite_id_or_name):
        identifier = str(favorite_id_or_name or "").strip()
        if not identifier:
            return None
        favorites = self.get_favorites()
        for favorite in favorites:
            if favorite.get("id") == identifier:
                return favorite
        for favorite in favorites:
            if favorite.get("name") == identifier:
                return favorite
        return None

    def upsert_favorite(self, favorite):
        normalized = normalize_favorite(favorite)
        favorites = self.get_favorites()
        identifier = normalized.get("id")
        for index, item in enumerate(favorites):
            if item.get("id") == identifier:
                favorites[index] = normalized
                break
        else:
            favorites.insert(0, normalized)
        self.set_favorites(favorites)
        return self.get_favorite(identifier)

    def get_favorite_run_history(self):
        history = self._normalize_favorite_run_history(self.config.get("favorite_run_history"))
        if history != self.config.get("favorite_run_history"):
            self.config["favorite_run_history"] = history
            self.save_config()
        return json.loads(json.dumps(history, ensure_ascii=False))

    def set_favorite_run_history(self, history):
        normalized = self._normalize_favorite_run_history(history)
        if normalized == self.config.get("favorite_run_history"):
            return
        self.config["favorite_run_history"] = normalized
        self.save_config()

    def append_favorite_run_history(self, record):
        history = self.get_favorite_run_history()
        history.insert(0, record)
        normalized = self._normalize_favorite_run_history(history)[:FAVORITE_RUN_HISTORY_LIMIT]
        self.config["favorite_run_history"] = normalized
        self.save_config()
        return normalized[0] if normalized else None

    def get_projects(self, include_hidden=True):
        projects = self._normalize_projects(self.config.get("projects"))
        if projects != self.config.get("projects"):
            self.config["projects"] = projects
            self.save_config()
        if not include_hidden:
            projects = [item for item in projects if not item.get("archived") and not item.get("hidden")]
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

    def upsert_mcp_servers(self, servers):
        current = self.get_mcp_servers()
        incoming = self._normalize_mcp_servers(servers)
        by_id = {str(server.get("id") or "").strip(): index for index, server in enumerate(current)}
        added = 0
        replaced = 0
        for server in incoming:
            server_id = str(server.get("id") or "").strip()
            if server_id and server_id in by_id:
                current[by_id[server_id]] = server
                replaced += 1
            else:
                current.append(server)
                added += 1
        self.set_mcp_servers(current)
        return {"added": added, "replaced": replaced, "servers": self.get_mcp_servers()}

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
        if name == "superset-mcp":
            clear_mcp_auth_cache()
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
            changed = False
            if name is not None:
                next_name = str(name or "").strip() or os.path.basename(normalized_path.rstrip(os.sep)) or normalized_path
                if project.get("name") != next_name:
                    project["name"] = next_name
                    changed = True
            if pinned is not None:
                next_pinned = bool(pinned)
                if bool(project.get("pinned")) != next_pinned:
                    project["pinned"] = next_pinned
                    changed = True
            if bool(project.get("archived")):
                project["archived"] = False
                changed = True
            if int(project.get("archived_at") or 0):
                project["archived_at"] = 0
                changed = True
            if bool(project.get("hidden")):
                project["hidden"] = False
                changed = True
            if self._normalize_project_path(project.get("path")) != normalized_path:
                project["path"] = normalized_path
                changed = True
            if changed:
                project["updated_at"] = now
                self.set_projects(projects)
            updated = project
            break
        if updated is None:
            updated = {
                "path": normalized_path,
                "name": str(name or "").strip() or os.path.basename(normalized_path.rstrip(os.sep)) or normalized_path,
                "pinned": bool(pinned) if pinned is not None else False,
                "archived": False,
                "archived_at": 0,
                "hidden": False,
                "created_at": now,
                "updated_at": now,
            }
            projects.append(updated)
            self.set_projects(projects)
        return json.loads(json.dumps(updated, ensure_ascii=False))

    def archive_project(self, path):
        normalized_path = self._normalize_project_path(path)
        if not normalized_path:
            return False
        projects = self.get_projects(include_hidden=True)
        key = self._project_key(normalized_path)
        changed = False
        now = int(time.time())
        for project in projects:
            if self._project_key(project.get("path")) != key:
                continue
            project["archived"] = True
            project["archived_at"] = now
            project["hidden"] = True
            project["updated_at"] = now
            changed = True
            break
        if not changed:
            projects.append(
                {
                    "path": normalized_path,
                    "name": os.path.basename(normalized_path.rstrip(os.sep)) or normalized_path,
                    "pinned": False,
                    "archived": True,
                    "archived_at": now,
                    "hidden": True,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            changed = True
        self.set_projects(projects)
        return changed

    def hide_project(self, path):
        return self.archive_project(path)

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
            project["archived"] = False
            project["archived_at"] = 0
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

    def _write_config_or_raise(self):
        os.makedirs(self.data_dir, exist_ok=True)
        temp_path = f"{self.config_path}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(self.config, handle, indent=4, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.config_path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

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
        managed_servers = self._normalize_mcp_servers(self.config.get("mcp_servers"))
        managed_updated = False
        for server in managed_servers:
            if str(server.get("source_skill") or "").strip() != str(skill_name or "").strip():
                continue
            if bool(server.get("enabled", True)) == bool(enabled):
                continue
            server["enabled"] = bool(enabled)
            managed_updated = True
        if managed_updated:
            self.config["mcp_servers"] = managed_servers
        self.save_config()
