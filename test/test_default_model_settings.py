import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QFont, QFontDatabase, QPalette

from core.config_manager import ConfigManager
from core.theme import DesignTokens, ThemeRuntimeManager, default_design_tokens
from core.theme_service import ThemeRepository
from main import ModelChannelManager, SettingsDialog


class DefaultModelSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if os.environ.get("COWORK_DEFAULT_MODEL_SCREENSHOTS"):
            if "Microsoft YaHei UI" not in QFontDatabase.families():
                font_path = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", "msyh.ttc")
                if QFontDatabase.addApplicationFont(font_path) < 0:
                    raise RuntimeError("无法加载截图所需的微软雅黑字体")
            cls.app.setFont(QFont("Microsoft YaHei UI", 9))

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for target in ("get_app_data_dir", "get_base_dir"):
            patcher = patch(f"core.config_manager.{target}", return_value=temp.name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config = ConfigManager()
        channels = self.config.get_model_channels()
        channels[0]["models"] = [
            {"id": "model-a", "model_name": "model-a", "display_name": "模型 A"},
            {"id": "model-b", "model_name": "model-b", "display_name": "模型 B"},
        ]
        self.config.set_model_channels(channels, "model-a")
        self.dialog = SettingsDialog(self.config, initial_page_label="模型与服务")
        self.addCleanup(self.close_dialog)
        self.manager = self.dialog.model_channel_manager
        self.combo = self.manager.default_model_combo

    def close_dialog(self):
        self.dialog._allow_close_without_prompt = True
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()

    def select_b(self):
        self.combo.setCurrentIndex(self.combo.findData("model-b"))
        self.app.processEvents()

    def capture(self, name, widget=None):
        directory = os.environ.get("COWORK_DEFAULT_MODEL_SCREENSHOTS")
        if directory:
            widget = widget or self.dialog
            os.makedirs(directory, exist_ok=True)
            widget.resize(820, 680)
            widget.show()
            self.app.processEvents()
            self.assertTrue(widget.grab().save(os.path.join(directory, name + ".png")))

    def test_save_default_persists_and_clears_dirty_state(self):
        self.assertEqual(self.combo.currentData(), "model-a")
        self.assertIn(" / 模型 A", self.combo.currentText())
        self.assertFalse(self.dialog._settings_dirty)
        self.select_b()
        self.assertTrue(self.dialog._settings_dirty)
        self.assertTrue(self.dialog.save_settings_btn.isEnabled())
        self.assertEqual(self.config.get_selected_model_id(), "model-a")
        self.capture("default-model-selected")
        with patch("main.log_model_service_event") as log:
            self.dialog.save_settings()
        self.assertEqual(ConfigManager().get_selected_model_id(), "model-b")
        self.assertFalse(self.dialog._settings_dirty)
        self.assertIn("default_model_save_completed", [call.args[0] for call in log.call_args_list])

    def test_cancel_does_not_change_saved_default(self):
        self.select_b()
        with patch("main.QMessageBox.question", return_value=QMessageBox.Yes):
            self.dialog.request_reject()
        self.assertEqual(ConfigManager().get_selected_model_id(), "model-a")

    def test_save_failure_retains_draft_and_previous_default(self):
        self.select_b()
        with patch.object(self.config, "_write_config", side_effect=[OSError("disk full"), None]), patch(
            "main.QMessageBox.critical"
        ) as error, patch("main.log_model_service_event") as log:
            self.dialog.save_settings()
        error.assert_called_once()
        self.assertEqual(self.combo.currentData(), "model-b")
        self.assertTrue(self.dialog._settings_dirty)
        self.assertEqual(self.config.get_selected_model_id(), "model-a")
        self.assertEqual(ConfigManager().get_selected_model_id(), "model-a")
        self.assertIn("default_model_save_failed", [call.args[0] for call in log.call_args_list])

    def test_deleted_default_requires_explicit_reselection(self):
        editor = self.manager.editors[0]
        editor._models().pop(0)
        editor.refresh_model_list()
        editor.changed.emit()
        self.assertEqual(self.combo.currentIndex(), -1)
        self.assertIn("重新选择", self.manager.default_model_hint.text())
        self.capture("default-model-deleted")
        with patch("main.QMessageBox.warning") as warning:
            self.dialog.save_settings()
        warning.assert_called_once()
        self.assertEqual(ConfigManager().get_selected_model_id(), "model-a")
        self.select_b()
        self.dialog.save_settings()
        self.assertEqual(ConfigManager().get_selected_model_id(), "model-b")

    def test_deleted_channel_requires_reselection(self):
        self.manager.add_channel()
        other = self.manager.editors[-1]
        other._models().append({"id": "other", "model_name": "other"})
        other.changed.emit()
        with patch("main.QMessageBox.question", return_value=QMessageBox.Yes):
            self.manager.delete_channel(self.manager.editors[0])
        self.assertEqual(self.combo.currentIndex(), -1)
        with patch("main.QMessageBox.warning") as warning:
            self.dialog.save_settings()
        warning.assert_called_once()

    def test_no_models_can_be_saved_without_default(self):
        with patch("main.QMessageBox.question", return_value=QMessageBox.Yes):
            while self.manager.editors:
                self.manager.delete_channel(self.manager.editors[0])
        self.assertFalse(self.combo.isEnabled())
        self.capture("default-model-empty")
        self.dialog.save_settings()
        self.assertEqual(ConfigManager().get_selected_model_id(), "")

    def test_rename_refreshes_label_without_changing_selection(self):
        editor = self.manager.editors[0]
        editor._models()[0]["display_name"] = "新名称"
        editor.display_name_input.setText("新渠道名")
        editor.changed.emit()
        self.assertEqual(self.combo.currentData(), "model-a")
        self.assertEqual(self.combo.currentText(), "新渠道名 / 新名称")

    def test_theme_preview_updates_combo_and_can_be_cancelled(self):
        original_font = self.app.font()
        original_style = self.app.styleSheet()
        original_tokens = {key: getattr(DesignTokens, key) for key in default_design_tokens()}
        original_registry = getattr(self.app, "theme_binding_registry", None)
        with tempfile.TemporaryDirectory() as directory:
            repository = ThemeRepository(directory)
            runtime = ThemeRuntimeManager(self.app, repository)
            manager = ModelChannelManager(self.config.get_model_channels(), selected_model_id="model-a")
            try:
                saved = repository.load()
                repository.write_preview(
                    name="默认模型主题测试",
                    overrides={"tokens": {"primary_soft": "#ddeeff"}},
                    default_tokens=default_design_tokens(),
                    session_id="test",
                )
                with patch("core.theme.QFontDatabase.families", return_value=["Microsoft YaHei UI", "Consolas"]):
                    self.assertTrue(runtime.apply_repository_state(reason="test_preview"))
                    self.assertEqual(manager.default_model_combo.view().palette().color(QPalette.Highlight).name(), "#ddeeff")
                    self.assertEqual(repository.load(), saved)
                    self.capture("default-model-theme-preview", manager)
                    self.assertTrue(runtime.restore_saved_theme())
                    self.capture("default-model-theme-restored", manager)
                self.assertIsNone(repository.load_preview())
                self.assertEqual(repository.load(), saved)
                self.assertEqual(manager.get_default_model_id(), "model-a")
                self.assertEqual(self.config.get_selected_model_id(), "model-a")
            finally:
                runtime.stop()
                manager.deleteLater()
                self.app.processEvents()
                self.app.theme_binding_registry = original_registry
                for key, value in original_tokens.items():
                    setattr(DesignTokens, key, value)
                self.app.setFont(original_font)
                self.app.setStyleSheet(original_style)


if __name__ == "__main__":
    unittest.main()
