"""Opening, searching and saving settings must not materialize unrelated pages."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget, QStackedWidget
from core.config_manager import ConfigManager
from main import SettingsDialog
from ui.settings_loading import PAGE_SPECS


class LazySettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name in ("get_app_data_dir", "get_base_dir"):
            patcher = patch("core.config_manager." + name, return_value=self.temp.name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.config = ConfigManager()
        self.host = QWidget()
        self.host.main_page_stack = QStackedWidget(self.host)
        self.pages = []
        self.addCleanup(self.dispose)

    def dispose(self):
        for page in self.pages:
            page._allow_close_without_prompt = True
            page.close()
        self.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def page(self, label=None, domain="settings"):
        page = SettingsDialog(self.config, self.host, label, domain)
        self.pages.append(page)
        return page

    def test_first_open_and_search_do_not_construct_hidden_editors(self):
        with patch("main.ModelChannelManager", side_effect=AssertionError("model construction")), \
                patch("main.MemoryStore", side_effect=AssertionError("memory read")):
            page = self.page()
            self.assertFalse(page.isWindow())
            self.assertEqual(set(page._loaded_pages), {"appearance"})
            self.assertIsNone(page.theme_settings_panel.editor_page)
            page.settings_search.setText("日志")
            self.assertTrue(page._settings_search_matches)
            self.assertEqual(set(page._loaded_pages), {"appearance"})
            page.save_settings()
            self.assertEqual(set(page._loaded_pages), {"appearance"})

    def test_nested_direct_entry_does_not_build_default_subpage(self):
        page = self.page("组件与依赖")
        self.assertEqual(set(page._loaded_pages), {"components"})
        self.assertEqual(page.advanced_section.current_label, "组件与依赖")
        page.select_initial_page("运行日志")
        self.assertEqual(set(page._loaded_pages), {"components", "logs"})

    def test_all_settings_pages_build_and_cache(self):
        page = self.page()
        for key in page._lazy_pages:
            with self.subTest(page=key):
                actual = page.ensure_page(key)
                self.assertIsNotNone(actual)
                self.assertIs(actual, page.ensure_page(key))
                self.assertFalse(page._settings_dirty)

    def test_new_page_does_not_clear_existing_draft_and_save_keeps_unopened_config(self):
        page = self.page("回答偏好")
        page.memory_soul_edit.setPlainText("保存这份偏好")
        channels = copy.deepcopy(self.config.get_model_channels())
        page.select_initial_page("文件与存储")
        self.assertTrue(page._settings_dirty)
        page.default_ws_input.setText(self.temp.name)
        page.save_settings()
        self.assertFalse(page._settings_dirty)
        self.assertEqual(page.memory_store.read_soul().strip(), "保存这份偏好")
        self.assertEqual(self.config.get_model_channels(), channels)
        self.assertEqual(set(page._loaded_pages), {"preferences", "storage"})

    def test_failed_save_retains_both_drafts(self):
        page = self.page("回答偏好")
        page.memory_soul_edit.setPlainText("失败后保留")
        page.select_initial_page("文件与存储")
        page.default_ws_input.setText(self.temp.name)
        with patch.object(self.config, "_write_config", side_effect=OSError("disk full")), patch("main.QMessageBox.critical"):
            page.save_settings()
        self.assertTrue(page._settings_dirty)
        self.assertEqual(page.memory_soul_edit.toPlainText(), "失败后保留")
        self.assertEqual(page.default_ws_input.text(), self.temp.name)

    def test_model_and_channel_details_are_explicit(self):
        page = self.page("模型与服务")
        manager = page.model_channel_manager
        self.assertEqual(manager.editors, [])
        channels = manager.get_channels()
        manager.validate_models()
        self.assertEqual(manager.editors, [])
        manager.open_service(channels[0]["channel_id"])
        self.assertEqual(len(manager.editors), 1)
        page.select_initial_page("消息连接")
        self.assertEqual(page.im_provider_panels, {})
        page._select_im_provider("wecom")
        self.assertEqual(set(page.im_provider_panels), {"wecom"})

    def test_discard_service_edit_restores_overview_without_rebuilding_details(self):
        page = self.page("模型与服务")
        manager = page.model_channel_manager
        original = manager.get_channels()
        manager.open_service(original[0]["channel_id"])
        manager.editors[0].display_name_input.setText("未保存的服务名称")
        self.assertTrue(page._settings_dirty)
        page.discard_page_changes()
        self.assertEqual(manager.get_channels(), original)
        self.assertEqual(manager.editors, [])
        self.assertIs(manager.route_stack.currentWidget(), manager.overview)

    def test_creation_failure_can_retry_without_losing_other_drafts(self):
        page = self.page("回答偏好")
        page.memory_soul_edit.setPlainText("保留草稿")
        with patch.object(page, "_build_storage", side_effect=RuntimeError("failed")):
            page.select_initial_page("文件与存储")
        self.assertNotIn("storage", page._loaded_pages)
        self.assertIsNotNone(page._lazy_pages["storage"].error)
        page._lazy_pages["storage"].retry()
        self.assertIn("storage", page._loaded_pages)
        self.assertTrue(page._settings_dirty)
        self.assertEqual(page.memory_soul_edit.toPlainText(), "保留草稿")

    def test_other_domains_do_not_construct_settings(self):
        for label in ("MCP 服务", "专家"):
            page = self.page(label, "capabilities")
            self.assertEqual(len(page._loaded_pages), 1)
            self.assertFalse(hasattr(page, "theme_settings_panel"))
            self.assertFalse(hasattr(page, "model_channel_manager"))
