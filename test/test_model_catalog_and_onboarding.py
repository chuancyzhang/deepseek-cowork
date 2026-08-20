import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication, QDialog

from core.config_manager import ConfigManager
from core.llm import model_catalog
from core.llm.model_catalog import ModelCatalogError
from main import (
    BatchModelCapabilityDialog,
    DeepSeekQuickstartDialog,
    MainWindow,
    ModelChannelEditor,
    ModelImportDialog,
    ModelSelectorPopover,
)


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


class ConfigQuickstartTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app_data = self.temp_dir.name

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
            [{"id": "deepseek-v4-pro"}, {"id": "deepseek-v4-flash"}],
        )
        self.assertTrue(config.has_usable_model_profile())
        self.assertEqual(config.get_selected_model_id(), "deepseek-v4-flash")
        self.assertEqual(profile["model_name"], "deepseek-v4-flash")
        self.assertEqual(profile["api_key"], "deepseek-key")
        self.assertEqual(profile["base_url"], "https://api.deepseek.com")

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
        channels[0]["models"][0]["supports_vision"] = True
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
        self.assertTrue(saved[0]["models"][0]["supports_vision"])
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


class ModelConfigurationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_import_dialog_prioritizes_recommendation_and_excludes_existing(self):
        dialog = ModelImportDialog(
            [
                {"id": "model-z", "owned_by": "owner"},
                {"id": "deepseek-v4-flash", "owned_by": "deepseek"},
                {"id": "model-existing", "owned_by": "owner"},
            ],
            existing_model_names=["model-existing"],
            recommended_model="deepseek-v4-flash",
        )
        try:
            self.assertIn("推荐", dialog.model_list.item(0).text())
            dialog._select_all_visible()
            self.assertEqual(
                {item["id"] for item in dialog.selected_models()},
                {"deepseek-v4-flash", "model-z"},
            )
        finally:
            dialog.close()

    def test_batch_dialog_only_returns_explicit_changes(self):
        dialog = BatchModelCapabilityDialog("openai", 2)
        try:
            self.assertEqual(dialog.changes(), {})
            dialog.vision_combo.setCurrentIndex(1)
            dialog.protocol_combo.setCurrentIndex(2)
            dialog.reasoning_mode_combo.setCurrentIndex(1)
            dialog.reasoning_checks["high"].setChecked(True)
            dialog.reasoning_checks["max"].setChecked(True)
            dialog.reasoning_combo.setCurrentIndex(dialog.reasoning_combo.findData("max"))
            changes = dialog.changes()
            self.assertTrue(changes["supports_vision"])
            self.assertEqual(changes["api_protocol"], "responses")
            self.assertEqual(changes["reasoning_efforts"], ["high", "max"])
            self.assertEqual(changes["reasoning_effort"], "max")
        finally:
            dialog.close()

    def test_editor_applies_batch_capabilities_to_selected_models(self):
        editor = ModelChannelEditor({
            "channel_id": "test-channel",
            "display_name": "Test",
            "provider_type": "openai",
            "api_key": "key",
            "base_url": "https://example.com/v1",
            "models": [
                {"id": "one", "model_name": "one"},
                {"id": "two", "model_name": "two"},
            ],
        })
        try:
            for row in range(editor.model_list.count()):
                editor.model_list.item(row).setSelected(True)
            with patch("main.BatchModelCapabilityDialog") as dialog_type:
                dialog = dialog_type.return_value
                dialog.exec.return_value = QDialog.Accepted
                dialog.changes.return_value = {"supports_vision": True}
                editor.edit_model()
            self.assertTrue(all(model["supports_vision"] for model in editor._models()))
        finally:
            editor.close()

    def test_editor_keeps_selection_by_id_after_recommended_sorting(self):
        editor = ModelChannelEditor({
            "channel_id": "deepseek-official",
            "display_name": "DeepSeek",
            "provider_type": "openai",
            "api_key": "key",
            "base_url": "https://api.deepseek.com",
            "models": [
                {"id": "pro", "model_name": "deepseek-v4-pro"},
                {"id": "flash", "model_name": "deepseek-v4-flash"},
            ],
        })
        try:
            self.assertEqual(editor.model_list.item(0).data(256), "flash")
            editor.model_list.setCurrentRow(1)
            with patch("main.ModelEditDialog") as dialog_type:
                dialog = dialog_type.return_value
                dialog.exec.return_value = QDialog.Accepted
                dialog.get_model.return_value = {
                    "id": "pro",
                    "display_name": "Pro edited",
                    "model_name": "deepseek-v4-pro",
                }
                editor.edit_model()
            self.assertEqual(editor.model_list.currentItem().data(256), "pro")
        finally:
            editor.close()

    def test_model_selector_places_recommended_model_first(self):
        popover = ModelSelectorPopover([
            {
                "id": "pro",
                "model_name": "deepseek-v4-pro",
                "display_name": "Pro",
                "provider_type": "openai",
                "base_url": "https://api.deepseek.com",
            },
            {
                "id": "flash",
                "model_name": "deepseek-v4-flash",
                "display_name": "Flash",
                "provider_type": "openai",
                "base_url": "https://api.deepseek.com",
            },
        ])
        try:
            self.assertEqual(popover.model_list.item(0).data(256), "flash")
            self.assertIn("推荐", popover.model_list.item(0).text())
        finally:
            popover.close()

    def test_quickstart_dialog_saves_only_after_validated_models(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir", return_value=temp_dir
        ), patch("core.config_manager.get_base_dir", return_value=temp_dir), patch(
            "main.log_model_service_event"
        ):
            config = ConfigManager()
            dialog = DeepSeekQuickstartDialog(config)
            try:
                dialog._handle_validation_result(
                    {
                        "ok": True,
                        "models": [
                            {"id": "deepseek-v4-flash"},
                            {"id": "deepseek-v4-pro"},
                        ],
                    },
                    "deepseek-key",
                )
                self.assertEqual(dialog.configured_profile["model_name"], "deepseek-v4-flash")
                self.assertTrue(config.has_usable_model_profile())
                self.assertIn("正在使用 deepseek-v4-flash", dialog.status_label.text())
            finally:
                dialog.close()

    def test_onboarding_is_suppressed_when_a_usable_model_exists(self):
        class Stub:
            _show_model_onboarding_if_needed = MainWindow._show_model_onboarding_if_needed

        stub = Stub()
        stub._model_onboarding_shown = False
        stub.config_manager = MagicMock()
        stub.config_manager.has_usable_model_profile.return_value = True
        stub.add_system_toast = MagicMock()
        self.assertFalse(stub._show_model_onboarding_if_needed())
        self.assertFalse(stub._model_onboarding_shown)


if __name__ == "__main__":
    unittest.main()
