from .providers import OpenAIProvider, AnthropicProvider, MoonshotProvider
from .deepseek import (
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_DEEPSEEK_REASONING_EFFORT,
    DEFAULT_DEEPSEEK_THINKING_ENABLED,
)

class LLMFactory:
    @staticmethod
    def create_provider(config_manager, model_id=None):
        profile = None
        if hasattr(config_manager, "get_model_profile"):
            profile = config_manager.get_model_profile(model_id)
            if not isinstance(profile, dict):
                profile = None
        if profile:
            provider_type = str(profile.get("provider_type") or profile.get("provider") or "openai").lower()
            api_key = profile.get("api_key")
            base_url = profile.get("base_url")
            model_name = profile.get("model_name", DEFAULT_DEEPSEEK_MODEL)
        else:
            provider_type = config_manager.get("llm_provider", "openai").lower()
            api_key = config_manager.get("api_key")
            base_url = config_manager.get("base_url")
            model_name = config_manager.get("model_name", DEFAULT_DEEPSEEK_MODEL)
        deepseek_options = {
            "thinking_enabled": (
                profile.get("deepseek_thinking_enabled", DEFAULT_DEEPSEEK_THINKING_ENABLED)
                if profile
                else config_manager.get("deepseek_thinking_enabled", DEFAULT_DEEPSEEK_THINKING_ENABLED)
            ),
            "reasoning_effort": (
                profile.get("deepseek_reasoning_effort", DEFAULT_DEEPSEEK_REASONING_EFFORT)
                if profile
                else config_manager.get("deepseek_reasoning_effort", DEFAULT_DEEPSEEK_REASONING_EFFORT)
            ),
        }

        if provider_type == "anthropic":
            return AnthropicProvider(api_key, base_url, model_name)
        elif provider_type in ["moonshot", "kimi"]:
            return MoonshotProvider(api_key, base_url, model_name, **deepseek_options)
        else:
            return OpenAIProvider(api_key, base_url, model_name, **deepseek_options)
