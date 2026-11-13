import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


from core.config_manager import ConfigManager
from core.llm import model_catalog
from core.llm.model_catalog import ModelCatalogError


class _Page:
    def __init__(self, data, next_page=None):
        self.data = data
        self._next_page = next_page

    def has_next_page(self):
        return self._next_page is not None

    def get_next_page(self):
        return self._next_page


class _Models:
    def __init__(self, page=None, error=None):
        self.page = page
        self.error = error

    def list(self, **_kwargs):
        if self.error:
            raise self.error
        return self.page


class _Client:
    page = None
    error = None

    def __init__(self, **_kwargs):
        self.models = _Models(self.page, self.error)


class ModelCatalogTests(unittest.TestCase):
    def test_official_deepseek_url_detection_is_strict(self):
        self.assertTrue(model_catalog.is_deepseek_official_base_url("https://api.deepseek.com"))
        self.assertTrue(model_catalog.is_deepseek_official_base_url("https://api.deepseek.com/v1/"))
        self.assertFalse(model_catalog.is_deepseek_official_base_url("http://api.deepseek.com"))
        self.assertFalse(model_catalog.is_deepseek_official_base_url("https://deepseek.example.com"))

    def test_openai_catalog_paginates_deduplicates_and_sorts(self):
        second = _Page([SimpleNamespace(id="alpha", owned_by="owner-a")])
        _Client.page = _Page([
            SimpleNamespace(id="zeta", owned_by="owner-z"),
            SimpleNamespace(id="alpha", owned_by="duplicate"),
        ], second)
        _Client.error = None
        with patch.object(model_catalog, "OpenAI", _Client):
            result = model_catalog.list_available_models(
                "openai", "https://api.example.com/v1", "secret"
            )
        self.assertEqual([item["id"] for item in result], ["alpha", "zeta"])
        self.assertEqual(result[0]["owned_by"], "duplicate")

    def test_unsupported_provider_is_rejected_without_fallback(self):
        with self.assertRaisesRegex(ModelCatalogError, "不支持的服务类型"):
            model_catalog.list_available_models(
                "unknown", "https://api.example.com/v1", "secret"
            )

    def test_anthropic_catalog_uses_anthropic_client(self):
        _Client.page = _Page([SimpleNamespace(id="claude-test", display_name="Claude Test")])
        _Client.error = None
        with patch.object(model_catalog, "Anthropic", _Client):
            result = model_catalog.list_available_models(
                "anthropic", "https://api.anthropic.com", "secret"
            )
        self.assertEqual(result, [{"id": "claude-test", "owned_by": "Claude Test"}])

    def test_empty_catalog_is_an_explicit_error(self):
        _Client.page = _Page([])
        _Client.error = None
        with patch.object(model_catalog, "OpenAI", _Client):
            with self.assertRaisesRegex(ModelCatalogError, "没有提供任何模型"):
                model_catalog.list_available_models("openai", "https://example.com/v1", "secret")

    def test_provider_error_redacts_key_and_bearer_token(self):
        _Client.page = None
        _Client.error = RuntimeError("Authorization: Bearer secret-value failed secret-value")
        with patch.object(model_catalog, "OpenAI", _Client):
            with self.assertRaises(ModelCatalogError) as caught:
                model_catalog.list_available_models(
                    "openai", "https://example.com/v1", "secret-value"
                )
        self.assertNotIn("secret-value", str(caught.exception))
        self.assertIn("***", str(caught.exception))

    def test_recommendation_registry_is_the_single_selection_source(self):
        original = dict(model_catalog.MODEL_RECOMMENDATIONS[model_catalog.DEEPSEEK_RECOMMENDATION_KEY])
        try:
            model_catalog.MODEL_RECOMMENDATIONS[model_catalog.DEEPSEEK_RECOMMENDATION_KEY]["model_name"] = "deepseek-next"
            self.assertEqual(
                model_catalog.get_recommended_model("openai", "https://api.deepseek.com"),
                "deepseek-next",
            )
        finally:
            model_catalog.MODEL_RECOMMENDATIONS[model_catalog.DEEPSEEK_RECOMMENDATION_KEY] = original

    def test_new_model_defaults_only_apply_to_official_deepseek(self):
        vision = model_catalog.build_new_model_defaults(
            "openai", "https://api.deepseek.com/v1/", "deepseek-v4-flash-vision-exp"
        )
        flash = model_catalog.build_new_model_defaults(
            "openai", "https://api.deepseek.com", "deepseek-v4-flash"
        )
        third_party = model_catalog.build_new_model_defaults(
            "openai", "https://example.com/v1", "deepseek-v4-flash-vision-exp"
        )

        self.assertEqual(vision["api_protocol"], "responses")
        self.assertEqual(vision["reasoning_efforts"], ["high", "max"])
        self.assertEqual(vision["reasoning_effort"], "max")
        self.assertTrue(vision["supports_vision"])
        self.assertTrue(flash["supports_vision"])
        self.assertEqual(vision["bootstrap_plugin"], "deepseek_flash_minimal")
        self.assertEqual(flash["bootstrap_plugin"], "deepseek_flash_minimal")
        self.assertFalse(third_party.get("bootstrap_plugin"))
        self.assertEqual(third_party["api_protocol"], "chat_completions")
        self.assertEqual(third_party["reasoning_efforts"], [])
        self.assertFalse(third_party["supports_vision"])


class ConfigQuickstartTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app_data = self.temp_dir.name
        # Keep catalog fixtures stable when the product recommendation changes.
        recommendation = patch.dict(model_catalog.MODEL_RECOMMENDATIONS[model_catalog.DEEPSEEK_RECOMMENDATION_KEY],
                                    {"model_name": "deepseek-v4-flash"})
        recommendation.start()
        self.addCleanup(recommendation.stop)

    def _config(self):
        patches = (
            patch("core.config_manager.get_app_data_dir", return_value=self.app_data),
            patch("core.config_manager.get_base_dir", return_value=self.app_data),
        )
        for active in patches:
            active.start()
            self.addCleanup(active.stop)
        return ConfigManager()

    def test_quickstart_populates_empty_config_and_selects_recommendation(self):
        config = self._config()
        config.set_model_channels([], "")
        profile = config.apply_deepseek_quickstart(
            "deepseek-key",
            [
                {"id": "deepseek-v4-pro"},
                {"id": "deepseek-v4-flash"},
                {"id": "deepseek-v4-flash-vision-exp"},
            ],
        )
        self.assertTrue(config.has_usable_model_profile())
        self.assertEqual(config.get_selected_model_id(), "deepseek-v4-flash")
        self.assertEqual(profile["model_name"], "deepseek-v4-flash")
        self.assertEqual(profile["api_key"], "deepseek-key")
        self.assertEqual(profile["base_url"], "https://api.deepseek.com")
        models = {
            model["model_name"]: model
            for model in config.get_model_channels()[0]["models"]
        }
        for model in models.values():
            self.assertEqual(model["bootstrap_plugin"], "deepseek_flash_minimal")
            self.assertEqual(model["api_protocol"], "responses")
            self.assertEqual(model["reasoning_efforts"], ["high", "max"])
            self.assertEqual(model["reasoning_effort"], "max")
        self.assertTrue(models["deepseek-v4-flash-vision-exp"]["supports_vision"])
        self.assertTrue(models["deepseek-v4-flash"]["supports_vision"])
        self.assertTrue(models["deepseek-v4-pro"]["supports_vision"])

    def test_fresh_quickstart_persists_only_discovered_models(self):
        config = self._config()
        config.apply_deepseek_quickstart(
            "deepseek-key",
            [{"id": "deepseek-v4-flash"}, {"id": "deepseek-extra"}],
        )
        self.assertEqual(
            {model["model_name"] for model in config.get_model_channels()[0]["models"]},
            {"deepseek-v4-flash", "deepseek-extra"},
        )

    def test_bootstrap_default_and_explicit_opt_out_survive_config_reload(self):
        from bootstrap_plugins import resolve

        config = self._config()
        channels = [{
            "channel_id": channel_id, "provider_type": "openai", "base_url": base_url,
            "models": [{"id": channel_id + "-auto", "model_name": "future-official-model"},
                       {"id": channel_id + "-off", "model_name": "deepseek-flash", "bootstrap_plugin": ""}],
        } for channel_id, base_url in (("official", "https://api.deepseek.com/v1/"),
                                      ("proxy", "https://example.test/v1"))]
        config.set_model_channels(channels, "official-auto")
        restored = ConfigManager()
        for model_id, expected in (("official-auto", "deepseek_flash_minimal"),
                                   ("official-off", ""), ("proxy-auto", ""), ("proxy-off", "")):
            with self.subTest(model_id=model_id):
                profile = restored.get_model_profile(model_id)
                self.assertEqual(profile["bootstrap_plugin"], expected)
                self.assertEqual(resolve(profile, profile["model_name"]) is not None, bool(expected))

    def test_legacy_provider_config_uses_its_own_endpoint_for_bootstrap_default(self):
        config = self._config()
        for base_url, expected in (("https://api.deepseek.com", "deepseek_flash_minimal"),
                                   ("https://proxy.test/v1", "")):
            with self.subTest(base_url=base_url):
                normalized = config._normalize_provider_config("openai", {
                    "base_url": base_url, "models": [{"model_name": "deepseek-future"}],
                })
                self.assertEqual(normalized["models"][0]["bootstrap_plugin"], expected)
                empty = config._normalize_provider_config("openai", {"base_url": base_url})
                self.assertEqual(empty["models"][0]["bootstrap_plugin"], expected)
                config.config.update(llm_provider="openai", base_url=base_url, model_name="deepseek-future")
                legacy = config._normalize_model_provider_configs({})
                self.assertEqual(legacy["openai"]["models"][0]["bootstrap_plugin"], expected)

    def test_existing_official_recommendation_can_change_without_duplicate_defaults(self):
        original = dict(model_catalog.MODEL_RECOMMENDATIONS[model_catalog.DEEPSEEK_RECOMMENDATION_KEY])
        try:
            model_catalog.MODEL_RECOMMENDATIONS[model_catalog.DEEPSEEK_RECOMMENDATION_KEY]["model_name"] = "deepseek-v4-pro"
            config = self._config()
            names = [
                model["model_name"]
                for model in config.get_model_channels()[0]["models"]
            ]
            self.assertEqual(names, ["deepseek-v4-pro", "deepseek-v4-flash"])
            self.assertEqual(config.get_selected_model_id(), "deepseek-v4-pro")
        finally:
            model_catalog.MODEL_RECOMMENDATIONS[model_catalog.DEEPSEEK_RECOMMENDATION_KEY] = original

    def test_new_recommendation_is_derived_into_fresh_defaults(self):
        original = dict(model_catalog.MODEL_RECOMMENDATIONS[model_catalog.DEEPSEEK_RECOMMENDATION_KEY])
        try:
            model_catalog.MODEL_RECOMMENDATIONS[model_catalog.DEEPSEEK_RECOMMENDATION_KEY]["model_name"] = "deepseek-next"
            config = self._config()
            self.assertEqual(config.get_selected_model_id(), "deepseek-next")
            self.assertEqual(
                config.get_model_channels()[0]["models"][0]["model_name"],
                "deepseek-next",
            )
        finally:
            model_catalog.MODEL_RECOMMENDATIONS[model_catalog.DEEPSEEK_RECOMMENDATION_KEY] = original

    def test_quickstart_preserves_existing_capabilities_and_other_channels(self):
        config = self._config()
        channels = config.get_model_channels()
        channels[0]["models"][0].update({
            "supports_vision": True,
            "api_protocol": "chat_completions",
            "deepseek_thinking_enabled": True,
            "deepseek_reasoning_effort": "high",
            "reasoning_efforts": ["high"],
            "reasoning_effort": "high",
            "bootstrap_plugin": "",
        })
        channels.append({
            "channel_id": "other",
            "display_name": "Other",
            "provider_type": "openai",
            "api_key": "other-key",
            "base_url": "https://other.example/v1",
            "models": [{"id": "other-model", "model_name": "other-model"}],
        })
        config.set_model_channels(channels, "other-model")
        config.apply_deepseek_quickstart("deepseek-key", [{"id": "deepseek-v4-flash"}])
        saved = config.get_model_channels()
        existing = saved[0]["models"][0]
        self.assertTrue(existing["supports_vision"])
        self.assertEqual(existing["api_protocol"], "chat_completions")
        self.assertEqual(existing["reasoning_efforts"], ["high"])
        self.assertEqual(existing["reasoning_effort"], "high")
        self.assertEqual(existing["bootstrap_plugin"], "")
        self.assertTrue(any(channel["channel_id"] == "other" for channel in saved))

    def test_missing_recommendation_keeps_config_unchanged(self):
        config = self._config()
        before = config.get_model_channels()
        with self.assertRaisesRegex(ValueError, "未返回推荐模型"):
            config.apply_deepseek_quickstart("deepseek-key", [{"id": "deepseek-v4-pro"}])
        self.assertEqual(config.get_model_channels(), before)

    def test_atomic_write_failure_rolls_back_memory(self):
        config = self._config()
        before = config.get_model_channels()
        with patch.object(config, "_write_config_or_raise", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                config.apply_deepseek_quickstart(
                    "deepseek-key", [{"id": "deepseek-v4-flash"}]
                )
        self.assertEqual(config.get_model_channels(), before)
        self.assertFalse(config.has_usable_model_profile())




if __name__ == "__main__":
    unittest.main()
