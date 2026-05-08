import json
import os
import sys
import shutil
import re
import uuid
from .env_utils import get_app_data_dir, get_base_dir
from .llm.deepseek import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_DEEPSEEK_REASONING_EFFORT,
    DEFAULT_DEEPSEEK_THINKING_ENABLED,
    should_migrate_legacy_model,
)

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-5"


class ConfigManager:
    def __init__(self):
        self.config_file = "config.json"
        
        # Use centralized data directory logic
        self.data_dir = get_app_data_dir()
        self.config_path = os.path.join(self.data_dir, self.config_file)
        self._loaded_config_keys = set()
        
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
            "model_provider_configs": self._default_model_provider_configs(),
            "deepseek_thinking_enabled": DEFAULT_DEEPSEEK_THINKING_ENABLED,
            "deepseek_reasoning_effort": DEFAULT_DEEPSEEK_REASONING_EFFORT,
            "disabled_skills": [],
            "god_mode": False,
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

    def get_god_mode(self):
        return self.config.get("god_mode", False)

    def set_god_mode(self, enabled: bool):
        self.config["god_mode"] = enabled
        self.save_config()

    def get_chat_history_dir(self):
        default_dir = os.path.join(self.data_dir, 'chat_history')
        return self.config.get("chat_history_dir", default_dir)

    def set_chat_history_dir(self, path):
        self.config["chat_history_dir"] = path
        self.save_config()

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
        raw_provider_configs = (
            self.config.get("model_provider_configs")
            if "model_provider_configs" in self._loaded_config_keys
            else None
        )
        normalized_configs = self._normalize_model_provider_configs(
            raw_provider_configs
        )
        if normalized_configs != self.config.get("model_provider_configs"):
            self.config["model_provider_configs"] = normalized_configs
            updated = True
        if not self._get_profile_from_configs(
            normalized_configs,
            self.config.get("selected_model_id"),
        ):
            self.config["selected_model_id"] = self._first_model_id(normalized_configs)
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

    def _slug(self, value):
        text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-").lower()
        return text or uuid.uuid4().hex[:8]

    def _make_model_id(self, provider_id, model_name):
        return f"{provider_id}-{self._slug(model_name)}"

    def _normalize_model_entry(self, provider_id, model, index=0):
        source = dict(model or {})
        model_name = str(source.get("model_name") or source.get("name") or "").strip()
        if should_migrate_legacy_model(model_name):
            model_name = DEFAULT_DEEPSEEK_MODEL if provider_id == "openai" else DEFAULT_ANTHROPIC_MODEL
        display_name = str(source.get("display_name") or source.get("label") or model_name).strip()
        model_id = str(source.get("id") or "").strip() or self._make_model_id(provider_id, model_name)
        if not model_id:
            model_id = f"{provider_id}-model-{index + 1}"
        entry = {
            "id": model_id,
            "display_name": display_name or model_name,
            "model_name": model_name,
        }
        if provider_id == "openai":
            entry["deepseek_thinking_enabled"] = bool(
                source.get(
                    "deepseek_thinking_enabled",
                    source.get("thinking_enabled", DEFAULT_DEEPSEEK_THINKING_ENABLED),
                )
            )
            entry["deepseek_reasoning_effort"] = str(
                source.get(
                    "deepseek_reasoning_effort",
                    source.get("reasoning_effort", DEFAULT_DEEPSEEK_REASONING_EFFORT),
                )
                or DEFAULT_DEEPSEEK_REASONING_EFFORT
            )
        return entry

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

    def _first_model_id(self, provider_configs=None):
        provider_configs = provider_configs or self.config.get("model_provider_configs") or {}
        for provider_id in ("openai", "anthropic"):
            models = ((provider_configs.get(provider_id) or {}).get("models") or [])
            if models:
                return models[0].get("id")
        return "openai-default"

    def _get_profile_from_configs(self, provider_configs, model_id=None):
        selected_id = str(model_id or "").strip()
        for provider_id, provider_config in (provider_configs or {}).items():
            for model in (provider_config or {}).get("models") or []:
                if selected_id and model.get("id") != selected_id:
                    continue
                profile = dict(model)
                profile["provider"] = provider_id
                profile["provider_display_name"] = (provider_config or {}).get("display_name") or provider_id
                profile["api_key"] = (provider_config or {}).get("api_key") or ""
                profile["base_url"] = (provider_config or {}).get("base_url") or ""
                return profile
        return None

    def get_model_provider_configs(self):
        configs = self._normalize_model_provider_configs(self.config.get("model_provider_configs"))
        if configs != self.config.get("model_provider_configs"):
            self.config["model_provider_configs"] = configs
            self.save_config()
        return json.loads(json.dumps(configs, ensure_ascii=False))

    def set_model_provider_configs(self, provider_configs, selected_model_id=None):
        self.config["model_provider_configs"] = self._normalize_model_provider_configs(provider_configs)
        if selected_model_id is not None:
            self.config["selected_model_id"] = str(selected_model_id or "").strip()
        if not self.get_model_profile(self.config.get("selected_model_id")):
            self.config["selected_model_id"] = self._first_model_id(self.config["model_provider_configs"])
        self._sync_legacy_model_fields(save=False)
        self.save_config()

    def iter_model_profiles(self):
        configs = self.get_model_provider_configs()
        profiles = []
        for provider_id in ("openai", "anthropic"):
            provider_config = configs.get(provider_id) or {}
            for model in provider_config.get("models") or []:
                profile = dict(model)
                profile["provider"] = provider_id
                profile["provider_display_name"] = provider_config.get("display_name") or provider_id
                profile["api_key"] = provider_config.get("api_key") or ""
                profile["base_url"] = provider_config.get("base_url") or ""
                profiles.append(profile)
        return profiles

    def get_model_profile(self, model_id=None):
        configs = self.get_model_provider_configs()
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

    def get_model_label(self, model_id=None, include_provider=True):
        profile = self.get_model_profile(model_id)
        if not profile:
            return DEFAULT_DEEPSEEK_MODEL
        name = profile.get("display_name") or profile.get("model_name") or DEFAULT_DEEPSEEK_MODEL
        if not include_provider:
            return name
        provider = profile.get("provider_display_name") or profile.get("provider") or ""
        return f"{provider} / {name}" if provider else name

    def _sync_legacy_model_fields(self, save=True):
        profile = self.get_model_profile(self.config.get("selected_model_id"))
        if not profile:
            return False
        updates = {
            "selected_model_id": profile.get("id"),
            "llm_provider": profile.get("provider", "openai"),
            "api_key": profile.get("api_key", ""),
            "base_url": profile.get("base_url", ""),
            "model_name": profile.get("model_name", DEFAULT_DEEPSEEK_MODEL),
        }
        if profile.get("provider") == "openai":
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

    def save_config(self):
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving config: {e}")

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save_config()

    def is_skill_enabled(self, skill_name):
        return skill_name not in self.config.get("disabled_skills", [])

    def set_skill_enabled(self, skill_name, enabled):
        disabled = set(self.config.get("disabled_skills", []))
        if enabled:
            if skill_name in disabled:
                disabled.remove(skill_name)
        else:
            disabled.add(skill_name)
        self.config["disabled_skills"] = list(disabled)
        self.save_config()
