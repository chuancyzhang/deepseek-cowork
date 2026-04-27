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

    def test_create_provider_uses_new_default_model_name(self):
        self.config_data.pop("model_name", None)
        provider = LLMFactory.create_provider(self.mock_config)
        self.assertIsInstance(provider, OpenAIProvider)
        self.assertEqual(provider.model_name, DEFAULT_DEEPSEEK_MODEL)

if __name__ == '__main__':
    unittest.main()
