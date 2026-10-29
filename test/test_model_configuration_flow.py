"""User flows for the inline model editor and its conversation entry point."""

import copy
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QThread, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from core.config_manager import ConfigManager
from main import MainWindow, ModelSelectorPopover, SettingsDialog


class ModelConfigurationFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for name in ("get_app_data_dir", "get_base_dir"):
            patcher = patch("core.config_manager." + name, return_value=directory.name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config = ConfigManager()
        self.config.set_model_channels([{
            "channel_id": "service-a", "display_name": "测试服务", "provider_type": "openai",
            "base_url": "https://example.test/v1", "api_key": "test-key",
            "models": [{"id": "model-a", "model_name": "existing-model", "display_name": "原模型"}],
        }], "model-a")
        self.page = SettingsDialog(self.config, initial_page_label="模型与服务")
        self.manager = self.page.model_channel_manager
        self.addCleanup(self.dispose)

    def dispose(self):
        self.page._allow_close_without_prompt = True
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def draft(self):
        if not self.manager.editors:
            self.manager.add_channel()
            setup = self.manager.setup
            setup.mode.setCurrentIndex(setup.mode.findData("manual"))
            setup.url_input.setText("https://example.test/v1")
            setup.api_key.setText("test-key")
            setup.manual_without_discovery()
        editor = self.manager.open_new_model("service-a")
        form = editor.model_forms[editor._active_form_id]
        form.model_name_input.setText("custom-model")
        return editor, form

    def test_create_save_reopen_preserves_default_and_all_capabilities(self):
        self.manager.origin_session_id = "conversation-a"
        editor, form = self.draft()
        model_id = editor._active_form_id
        form.display_name_input.setText("自定义模型")
        form.api_protocol_combo.setCurrentIndex(form.api_protocol_combo.findData("responses"))
        form.vision_check.setChecked(True)
        form.image_generation_check.setChecked(True)
        form.thinking_check.setChecked(True)
        for key in ("none", "high", "max"):
            form.reasoning_checks[key].setChecked(True)
        form.reasoning_combo.setCurrentIndex(form.reasoning_combo.findData("high"))
        form.context_window_input.setText("128000")
        form.max_output_input.setText("8192")
        self.assertEqual(len(ConfigManager().iter_model_profiles()), 1)
        self.page.save_settings()
        restored = ConfigManager().get_model_profile(model_id)
        self.assertEqual(restored["id"], model_id)
        self.assertEqual(restored["reasoning_efforts"], ["none", "high", "max"])
        self.assertEqual(restored["reasoning_effort"], "high")
        self.assertEqual(restored["context_window_tokens"], 128000)
        self.assertEqual(restored["max_output_tokens"], 8192)
        self.assertTrue(restored["supports_vision"] and restored["supports_image_generation"])
        self.assertEqual(restored["api_protocol"], "responses")
        self.assertEqual(self.config.get_selected_model_id(), "model-a")
        self.assertFalse(self.page._settings_dirty)
        self.assertEqual(self.manager.saved_model_id, model_id)
        self.assertFalse(self.manager.saved_notice.isHidden())
        editor.show_model_list()
        editor.open_model_form(model_id)
        self.assertIs(editor.model_forms[model_id], form)

    def test_failed_save_keeps_complete_draft_and_retry_succeeds(self):
        editor, form = self.draft()
        model_id = editor._active_form_id
        before = copy.deepcopy(self.config.config)
        with patch.object(self.config, "_write_config", side_effect=[OSError("disk full"), None]), patch(
            "main.QMessageBox.critical"
        ) as warning:
            self.page.save_settings()
        warning.assert_called_once()
        self.assertEqual(self.config.config, before)
        self.assertEqual(form.model_name_input.text(), "custom-model")
        self.assertTrue(self.page._settings_dirty)
        self.assertTrue(self.manager.saved_notice.isHidden())
        self.page.save_settings()
        self.assertEqual(ConfigManager().get_model_profile(model_id)["id"], model_id)

    def test_multiple_manual_models_share_one_save_boundary(self):
        editor, first = self.draft()
        first_id = editor._active_form_id
        editor.show_model_list()
        editor.add_model()
        second_id = editor._active_form_id
        self.assertNotEqual(first_id, second_id)
        editor.model_forms[second_id].model_name_input.setText("second-model")
        self.assertEqual(first.model_name_input.text(), "custom-model")
        self.assertEqual(len(ConfigManager().iter_model_profiles()), 1)
        self.page.save_settings()
        self.assertEqual(len(ConfigManager().iter_model_profiles()), 3)
        self.assertEqual(self.config.get_selected_model_id(), "model-a")

    def test_hidden_invalid_draft_blocks_save_and_returns_to_error(self):
        editor, form = self.draft()
        form.context_window_input.setText("invalid-budget")
        editor.show_model_list()
        self.manager.add_channel()
        self.page.save_settings()
        self.assertIs(self.manager.channel_detail_stack.currentWidget(), editor)
        self.assertFalse(form.error_label.isHidden())
        self.assertTrue(form.advanced_button.isChecked())
        self.assertEqual(form.context_window_input.text(), "invalid-budget")
        self.assertEqual(len(ConfigManager().iter_model_profiles()), 1)
        self.assertTrue(self.page._settings_dirty)

    def test_protocol_dependency_and_effort_validation_preserve_input(self):
        _, form = self.draft()
        form.image_generation_check.setChecked(True)
        self.assertFalse(form.validate())
        self.assertIn("Responses", form.error_label.text())
        self.assertTrue(form.image_generation_check.isChecked())
        form.api_protocol_combo.setCurrentIndex(form.api_protocol_combo.findData("responses"))
        form.thinking_check.setChecked(True)
        self.assertFalse(form.validate())
        form.reasoning_checks["max"].setChecked(True)
        self.assertTrue(form.validate())
        self.assertEqual(form.get_model()["reasoning_effort"], "max")

    def test_switching_services_retains_draft_and_discard_removes_it(self):
        editor, form = self.draft()
        self.manager.add_channel()
        self.manager.channel_list.setCurrentRow(0)
        self.assertEqual(form.model_name_input.text(), "custom-model")
        with patch("main.ProductMessageDialog.exec_result", return_value="discard"):
            self.assertTrue(self.page._confirm_discard_settings())
        self.assertEqual(len(self.manager.editors), 1)
        self.assertEqual(self.manager.editors[0].model_forms, {})
        self.assertEqual(len(self.manager.get_channels()[0]["models"]), 1)
        self.assertFalse(self.page._settings_dirty)

    def test_first_model_requests_explicit_default_without_hiding_the_choice(self):
        self.config.set_model_channels([], "")
        self.page.discard_page_changes()
        # Restore the externally emptied catalog before beginning a new draft.
        state = self.page._settings_state()["config"]
        state.update(model_channels=[], selected_model_id="")
        self.page._restore_configuration_editors(state)
        self.page._clear_settings_dirty()
        editor, form = self.draft()
        with patch("main.QMessageBox.warning") as warning:
            self.page.save_settings()
        warning.assert_called_once()
        self.assertFalse(self.manager.default_model_combo.isHidden())
        self.assertEqual(self.manager.default_model_combo.currentIndex(), -1)
        self.assertEqual(form.model_name_input.text(), "custom-model")
        self.manager.default_model_combo.setCurrentIndex(0)
        self.page.save_settings()
        self.assertEqual(ConfigManager().get_selected_model_id(), editor._models()[0]["id"])

    def test_discard_during_connection_test_keeps_thread_alive_until_finished(self):
        class DelayedTest(QThread):
            finished_signal = Signal(dict)

            def __init__(self, profile, parent):
                super().__init__(parent)

            def run(self):
                self.msleep(60)
                self.finished_signal.emit({"ok": True, "elapsed": 0.06})

        editor, _form = self.draft()
        with patch("main.ModelConnectionTestWorker", DelayedTest):
            editor.test_current_model()
        worker = editor.test_worker
        self.assertIs(worker.parent(), self.app)
        with patch("main.ProductMessageDialog.exec_result", return_value="discard"):
            self.assertTrue(self.page._confirm_discard_settings())
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(worker.wait(2000))
        self.app.processEvents()
        self.assertEqual(self.manager.editors[0].model_forms, {})
        self.assertEqual(len(ConfigManager().iter_model_profiles()), 1)

    def test_delete_edited_model_does_not_leave_invalid_cached_form(self):
        editor, form = self.draft()
        form.context_window_input.setText("bad")
        model_id = editor._active_form_id
        editor.show_model_list()
        for row in range(editor.model_list.count()):
            if editor.model_list.item(row).data(Qt.UserRole) == model_id:
                editor.model_list.setCurrentRow(row)
        editor.delete_model()
        self.assertNotIn(model_id, editor.model_forms)
        self.assertTrue(self.manager.validate_models())

    def test_anthropic_uses_existing_capabilities_and_hides_openai_options(self):
        editor, form = self.draft()
        editor.provider_combo.setCurrentIndex(editor.provider_combo.findData("anthropic"))
        form.vision_check.setChecked(True)
        self.assertTrue(form.protocol_field.isHidden())
        self.assertTrue(form.thinking_check.isHidden())
        self.assertTrue(form.validate())
        self.page.save_settings()
        profile = ConfigManager().get_model_profile(editor._active_form_id)
        self.assertEqual(profile["provider_type"], "anthropic")
        self.assertTrue(profile["supports_vision"])

    def test_model_row_actions_target_the_checked_model(self):
        editor, _form = self.draft()
        created_id = editor._active_form_id
        editor.show_model_list()
        editor._model_rows["model-a"].check.click()
        editor._model_rows[created_id].check.click()
        self.assertEqual(editor._selected_model_ids(), ["model-a"])
        self.assertEqual(editor.model_list.currentItem().data(Qt.UserRole), "model-a")
        with patch.object(editor, "test_current_model") as test_model:
            editor._model_rows[created_id].test_button.click()
            test_model.assert_called_once()
            self.assertEqual(editor.model_list.currentItem().data(Qt.UserRole), created_id)
        editor._model_rows["model-a"].name_button.click()
        self.assertEqual(editor._active_form_id, "model-a")
        editor.show_model_list()
        editor.delete_model()
        self.assertEqual([model["id"] for model in editor._models()], [created_id])

    def test_switch_settings_keeps_model_draft_and_sidebar_navigation(self):
        self.page.resize(1280, 900)
        self.page.show()
        self.app.processEvents()
        editor, form = self.draft()
        form.display_name_input.setText("保留这份草稿")
        self.page.nav_list.setCurrentRow(0)
        self.assertEqual(self.page._page_labels[self.page._active_settings_row], "外观与阅读")
        self.assertFalse(self.page.nav_list.isHidden())
        self.page.select_initial_page("模型与服务")
        self.assertFalse(self.page.nav_list.isHidden())
        self.assertFalse(self.page.settings_search.isHidden())
        self.assertIs(self.manager.route_stack.currentWidget(), self.manager.detail)
        self.assertTrue(self.manager.channel_list.isHidden())
        self.assertIs(self.manager.channel_detail_stack.currentWidget(), editor)
        self.assertEqual(form.display_name_input.text(), "保留这份草稿")
        self.assertTrue(self.page._settings_dirty)

    def test_return_uses_origin_session_and_rejects_deleted_model(self):
        stub = SimpleNamespace(
            PAGE_SETTINGS="settings", product_pages={"settings": self.page},
            sessions={"original": object(), "other": object()}, current_session_id="other",
            config_manager=self.config, show_conversation_page=MagicMock(return_value=True),
            on_model_selection_changed=MagicMock(return_value=True), add_system_toast=MagicMock(),
        )
        stub.activate_session = lambda origin: setattr(stub, "current_session_id", origin)
        self.manager.origin_session_id = "original"
        self.assertTrue(MainWindow.use_configured_model(stub, "model-a"))
        self.assertEqual(stub.current_session_id, "original")
        stub.on_model_selection_changed.assert_called_once_with("model-a")
        stub.on_model_selection_changed.reset_mock()
        self.assertFalse(MainWindow.use_configured_model(stub, "deleted-id"))
        stub.on_model_selection_changed.assert_not_called()
        self.assertEqual(self.config.get_selected_model_id(), "model-a")

    def test_empty_and_no_match_pickers_always_offer_configuration(self):
        host = QWidget()
        host.resize(720, 650)
        anchor = QPushButton("模型", host)
        anchor.setGeometry(500, 560, 100, 32)
        host.show()
        for profiles in ([], self.config.iter_model_profiles()):
            picker = ModelSelectorPopover(profiles, "model-a", host)
            picker.show_for(anchor)
            picker.search_input.setText("no-such-model")
            self.app.processEvents()
            self.assertTrue(picker.configure_btn.isVisible())
            self.assertTrue(picker.empty_label.isVisible())
            configured = []
            picker.configureRequested.connect(lambda: configured.append(True))
            QTest.mouseClick(picker.configure_btn, Qt.LeftButton)
            self.assertEqual(configured, [True])
            self.assertFalse(picker.isVisible())
        host.deleteLater()

    def test_picker_outside_click_keeps_clicked_focus_and_escape_closes(self):
        host = QWidget()
        host.resize(720, 650)
        anchor = QPushButton("模型", host)
        anchor.setGeometry(500, 560, 100, 32)
        outside = QPushButton("其他操作", host)
        outside.setGeometry(10, 10, 100, 32)
        host.show()
        self.app.processEvents()
        picker = ModelSelectorPopover(self.config.iter_model_profiles(), "model-a", host)
        picker.show_for(anchor)
        QTest.mouseClick(picker.search_input, Qt.LeftButton)
        self.assertTrue(picker.isVisible())
        QTest.mouseClick(outside, Qt.LeftButton)
        self.assertFalse(picker.isVisible())
        self.assertTrue(outside.hasFocus())
        picker = ModelSelectorPopover([], parent=host)
        picker.show_for(anchor)
        QTest.keyClick(picker.search_input, Qt.Key_Escape)
        self.assertFalse(picker.isVisible())
        host.deleteLater()


if __name__ == "__main__":
    unittest.main()
