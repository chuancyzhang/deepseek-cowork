from .providers import OpenAIProvider, AnthropicProvider

class LLMFactory:
    @staticmethod
    def create_provider(config_manager):
        provider_type = config_manager.get("llm_provider", "openai").lower()
        api_key = config_manager.get("api_key")
        base_url = config_manager.get("base_url")
        model_name = config_manager.get("model_name", "deepseek-reasoner")

        # Allow per-model config override if implemented in ConfigManager later
        # For now, we use the global keys but support the 'llm_provider' switch

        if provider_type == "anthropic":
            return AnthropicProvider(api_key, base_url, model_name)
        else:
            return OpenAIProvider(api_key, base_url, model_name)
