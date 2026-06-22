import sys
import os
import unittest
from unittest.mock import MagicMock
from types import ModuleType

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.llm.factory import LLMFactory
from core.llm.providers import OpenAIProvider, AnthropicProvider
from core.config_manager import ConfigManager
from core.llm.deepseek import DEFAULT_DEEPSEEK_MODEL

class TestLLMFactory(unittest.TestCase):
    def setUp(self):
        self.mock_config = MagicMock(spec=ConfigManager)
        self.mock_config.get.side_effect = self._config_get
        self.mock_config.get_model_profile.return_value = None

        self.config_data = {
            "api_key": "test_key",
            "base_url": "https://test.url",
            "model_name": "test-model",
            "llm_provider": "openai"
        }
        self.openai_module = ModuleType("openai")
        self.openai_module.OpenAI = MagicMock()
        self.anthropic_module = ModuleType("anthropic")
        self.anthropic_module.Anthropic = MagicMock()
        self.anthropic_module.omit = object()
        self.module_patcher = unittest.mock.patch.dict(
            sys.modules,
            {"openai": self.openai_module, "anthropic": self.anthropic_module},
        )
        self.module_patcher.start()

    def tearDown(self):
        self.module_patcher.stop()

    def _config_get(self, key, default=None):
        return self.config_data.get(key, default)

    def test_create_openai_provider(self):
        self.config_data["llm_provider"] = "openai"
        provider = LLMFactory.create_provider(self.mock_config)
        self.assertIsInstance(provider, OpenAIProvider)
        self.assertEqual(provider.model_name, "test-model")

    def test_create_anthropic_provider(self):
        self.config_data["llm_provider"] = "anthropic"
        provider = LLMFactory.create_provider(self.mock_config)
        self.assertIsInstance(provider, AnthropicProvider)
        self.assertEqual(provider.model_name, "test-model")
        self.anthropic_module.Anthropic.assert_called_with(
            api_key="test_key",
            base_url="https://test.url",
        )

    def test_create_anthropic_provider_uses_bearer_for_tencent_coding_endpoint(self):
        self.config_data["llm_provider"] = "anthropic"
        self.config_data["api_key"] = "Bearer coding-token"
        self.config_data["base_url"] = "https://api.lkeap.cloud.tencent.com/coding/anthropic"

        provider = LLMFactory.create_provider(self.mock_config)

        self.assertIsInstance(provider, AnthropicProvider)
        self.anthropic_module.Anthropic.assert_called_with(
            auth_token="coding-token",
            base_url="https://api.lkeap.cloud.tencent.com/coding/anthropic",
            default_headers={"X-Api-Key": self.anthropic_module.omit},
        )

    def test_create_provider_uses_new_default_model_name(self):
        self.config_data.pop("model_name", None)
        provider = LLMFactory.create_provider(self.mock_config)
        self.assertIsInstance(provider, OpenAIProvider)
        self.assertEqual(provider.model_name, DEFAULT_DEEPSEEK_MODEL)

    def test_create_provider_uses_selected_model_profile(self):
        self.mock_config.get_model_profile.return_value = {
            "provider_type": "openai",
            "channel_id": "custom-openai",
            "channel_display_name": "Custom OpenAI",
            "api_key": "profile_key",
            "base_url": "https://profile.url",
            "model_name": "profile-model",
            "deepseek_thinking_enabled": False,
            "deepseek_reasoning_effort": "max",
        }

        provider = LLMFactory.create_provider(self.mock_config, "openai-profile")

        self.assertIsInstance(provider, OpenAIProvider)
        self.assertEqual(provider.model_name, "profile-model")
        self.openai_module.OpenAI.assert_called_with(
            api_key="profile_key",
            base_url="https://profile.url",
        )
        self.assertFalse(provider.thinking_enabled)
        self.assertEqual(provider.reasoning_effort, "max")
        self.assertFalse(provider.supports_vision)

    def test_create_provider_accepts_runtime_reasoning_override(self):
        self.mock_config.get_model_profile.return_value = {
            "provider_type": "openai",
            "api_key": "profile_key",
            "base_url": "https://profile.url",
            "model_name": "profile-model",
            "reasoning_effort": "medium",
        }

        provider = LLMFactory.create_provider(
            self.mock_config,
            "openai-profile",
            reasoning_effort="xhigh",
        )

        self.assertEqual(provider.reasoning_effort, "xhigh")

    def test_create_provider_from_unsaved_profile(self):
        provider = LLMFactory.create_provider_from_profile({
            "provider_type": "openai",
            "api_key": "draft-key",
            "base_url": "https://draft.url",
            "model_name": "draft-model",
            "reasoning_effort": "low",
        })

        self.assertIsInstance(provider, OpenAIProvider)
        self.assertEqual(provider.model_name, "draft-model")
        self.assertEqual(provider.reasoning_effort, "low")

    def test_create_provider_uses_anthropic_profile(self):
        self.mock_config.get_model_profile.return_value = {
            "provider_type": "anthropic",
            "channel_id": "custom-anthropic",
            "channel_display_name": "Custom Anthropic",
            "api_key": "anthropic_key",
            "base_url": "https://anthropic.url",
            "model_name": "claude-test",
            "supports_vision": True,
        }

        provider = LLMFactory.create_provider(self.mock_config, "anthropic-profile")

        self.assertIsInstance(provider, AnthropicProvider)
        self.assertEqual(provider.model_name, "claude-test")
        self.assertTrue(provider.supports_vision)
        self.anthropic_module.Anthropic.assert_called_with(
            api_key="anthropic_key",
            base_url="https://anthropic.url",
        )

if __name__ == '__main__':
    unittest.main()
