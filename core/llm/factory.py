from .providers import OpenAIProvider, AnthropicProvider, MoonshotProvider
from .deepseek import (
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_DEEPSEEK_REASONING_EFFORT,
    DEFAULT_DEEPSEEK_THINKING_ENABLED,
)

class LLMFactory:
    @staticmethod
    def create_provider(config_manager):
        provider_type = config_manager.get("llm_provider", "openai").lower()
        api_key = config_manager.get("api_key")
        base_url = config_manager.get("base_url")
        model_name = config_manager.get("model_name", DEFAULT_DEEPSEEK_MODEL)
        deepseek_options = {
            "thinking_enabled": config_manager.get("deepseek_thinking_enabled", DEFAULT_DEEPSEEK_THINKING_ENABLED),
            "reasoning_effort": config_manager.get("deepseek_reasoning_effort", DEFAULT_DEEPSEEK_REASONING_EFFORT),
        }

        # Allow per-model config override if implemented in ConfigManager later
        # For now, we use the global keys but support the 'llm_provider' switch

        if provider_type == "anthropic":
            return AnthropicProvider(api_key, base_url, model_name)
        elif provider_type in ["moonshot", "kimi"]:
            return MoonshotProvider(api_key, base_url, model_name, **deepseek_options)
        else:
            return OpenAIProvider(api_key, base_url, model_name, **deepseek_options)
