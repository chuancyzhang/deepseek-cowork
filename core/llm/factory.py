from .providers import OpenAIProvider, AnthropicProvider, MoonshotProvider
from .deepseek import (
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_DEEPSEEK_REASONING_EFFORT,
    DEFAULT_DEEPSEEK_THINKING_ENABLED,
)

class LLMFactory:
    @staticmethod
    def create_provider(config_manager, model_id=None, reasoning_effort=None):
        profile = None
        if hasattr(config_manager, "get_model_profile"):
            profile = config_manager.get_model_profile(model_id)
            if not isinstance(profile, dict):
                profile = None
        if not profile:
            profile = {
                "provider_type": config_manager.get("llm_provider", "openai"),
                "api_key": config_manager.get("api_key"),
                "base_url": config_manager.get("base_url"),
                "model_name": config_manager.get("model_name", DEFAULT_DEEPSEEK_MODEL),
                "deepseek_thinking_enabled": config_manager.get(
                    "deepseek_thinking_enabled", DEFAULT_DEEPSEEK_THINKING_ENABLED
                ),
                "reasoning_effort": config_manager.get(
                    "deepseek_reasoning_effort", DEFAULT_DEEPSEEK_REASONING_EFFORT
                ),
                "supports_vision": config_manager.get("supports_vision", False),
                "stream_usage_enabled": config_manager.get("stream_usage_enabled", True),
                "prompt_cache_key_param": config_manager.get("prompt_cache_key_param", ""),
            }
        return LLMFactory.create_provider_from_profile(profile, reasoning_effort=reasoning_effort)

    @staticmethod
    def create_provider_from_profile(profile, reasoning_effort=None):
        profile = dict(profile or {})
        provider_type = str(profile.get("provider_type") or profile.get("provider") or "openai").lower()
        api_key = profile.get("api_key")
        base_url = profile.get("base_url")
        model_name = profile.get("model_name", DEFAULT_DEEPSEEK_MODEL)
        deepseek_options = {
            "thinking_enabled": profile.get(
                "deepseek_thinking_enabled", DEFAULT_DEEPSEEK_THINKING_ENABLED
            ),
            "reasoning_effort": (
                reasoning_effort
                if reasoning_effort is not None
                else profile.get("reasoning_effort", profile.get("deepseek_reasoning_effort", ""))
            ),
            "supports_vision": profile.get("supports_vision", False),
            "stream_usage_enabled": profile.get("stream_usage_enabled", True),
            "prompt_cache_key_param": profile.get("prompt_cache_key_param", ""),
        }

        if provider_type == "anthropic":
            return AnthropicProvider(
                api_key,
                base_url,
                model_name,
                supports_vision=deepseek_options["supports_vision"],
            )
        elif provider_type in ["moonshot", "kimi"]:
            return MoonshotProvider(api_key, base_url, model_name, **deepseek_options)
        else:
            return OpenAIProvider(api_key, base_url, model_name, **deepseek_options)
