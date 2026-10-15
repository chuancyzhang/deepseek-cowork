import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from core.config_manager import ConfigManager
from core.settings_changes import merge_settings_objects
from main import SettingsDialog, AgentProfileManager, SkillsCenterDialog, McpServerEditDialog


class UnifiedManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for target in ("get_app_data_dir", "get_base_dir"):
            p = patch("core.config_manager." + target, return_value=self.temp.name)
            p.start()
            self.addCleanup(p.stop)
        self.config = ConfigManager()

    def page(self, domain="settings", section=None):
        page = SettingsDialog(self.config, domain=domain, initial_page_label=section)
        page.setProperty("embeddedProductPage", True)
        def close():
            page._allow_close_without_prompt = True
            page.close()
            page.deleteLater()
            self.app.processEvents()
        self.addCleanup(close)
        return page

    def test_domains_have_one_destination_per_feature(self):
        settings = self.page()
        self.assertNotIn("记忆", settings._page_labels)
        self.assertNotIn("专家", settings._page_labels)
        self.assertIn("回答偏好", settings._page_labels)
        self.assertTrue(settings.discard_settings_btn.isHidden())
        with patch.object(settings, "_restore_configuration_editors", side_effect=AssertionError("unchanged restore")):
            settings.discard_page_changes()
        self.assertEqual(self.page("capabilities")._page_labels, ["MCP 服务", "专家"])
        self.assertEqual(self.page("projects")._page_labels, ["项目", "对话", "记忆", "归档"])

    def test_save_workspace_preserves_external_model_and_agent_changes(self):
        page = self.page(section="工作区与存储")
        original = self.config.get_model_channels()
        changed = self.config.get_model_channels()
        changed[0]["display_name"] = "后台更新"
        self.config.set_model_channels(changed, self.config.get_selected_model_id())
        self.config.set_agent_profiles([{"id": "external", "name": "后台角色", "enabled": False}])
        page.default_ws_input.setText(self.temp.name)
        with patch("main.QMessageBox.question", return_value=16384):
            page.save_settings()
        self.assertEqual(self.config.get_model_channels()[0]["display_name"], "后台更新")
        self.assertEqual(self.config.get_agent_profiles()[0]["id"], "external")
        self.assertFalse(page._settings_dirty)

    def test_switch_cancel_retains_input_and_selected_page(self):
        page = self.page(section="回答偏好")
        page.memory_soul_edit.setPlainText("草稿")
        with patch("main.ProductMessageDialog.exec_result", return_value="stay"):
            page.select_initial_page("外观")
        self.assertEqual(page.nav_list.currentItem().text(), "回答偏好")
        self.assertEqual(page.memory_soul_edit.toPlainText(), "草稿")
        with patch("main.ProductMessageDialog.exec_result", return_value="discard"):
            page.select_initial_page("外观")
        self.assertEqual(page.nav_list.currentItem().text(), "外观")
        self.assertFalse(page._settings_dirty)

    def test_workspace_memory_does_not_cross_projects(self):
        page = self.page("projects", "记忆")
        first, second = os.path.join(self.temp.name, "first"), os.path.join(self.temp.name, "second")
        page.select_memory_workspace(first)
        page.memory_workspace_edit.setPlainText("项目一")
        page.save_settings()
        page.select_memory_workspace(second)
        self.assertEqual(page.memory_workspace_edit.toPlainText(), "")
        page.memory_workspace_edit.setPlainText("项目二")
        page.save_settings()
        page.select_memory_workspace(first)
        self.assertEqual(page.memory_workspace_edit.toPlainText().strip(), "项目一")

    def test_untouched_agent_draft_is_clean_and_discard_is_local(self):
        page = self.page("capabilities", "智能体")
        manager = page.agent_profile_manager
        manager.add_profile()
        self.assertFalse(page._settings_dirty)
        self.assertEqual(manager.get_profiles(), [])
        with patch("main.ProductMessageDialog.exec_result", side_effect=AssertionError("untouched prompt")):
            self.assertTrue(manager.leave_profile_editor())
        self.assertTrue(all(profile.get("builtin") for profile in manager.profiles))
        manager.add_profile()
        manager.name_input.setText("中文角色")
        self.assertTrue(page._settings_dirty)
        with patch.object(page.theme_settings_panel, "restore_saved_theme", side_effect=AssertionError("theme reload")):
            page.discard_page_changes()
        self.assertFalse(page._settings_dirty)
        self.assertEqual(manager.get_profiles(), [])

    def test_agent_edit_retains_unavailable_capability(self):
        manager = AgentProfileManager([{"id": "a", "name": "角色", "skill_names": ["missing"]}], lambda: [{"name": "available", "description": "可用技能"}])
        self.addCleanup(manager.deleteLater)
        manager.profile_list.setCurrentRow(next(i for i, profile in enumerate(manager.profiles) if profile["id"] == "a"))
        manager.name_input.setText("新名称")
        self.assertEqual(manager.get_profiles()[0]["skill_names"], ["missing"])

    def test_object_merge_preserves_unrelated_live_changes_and_rejects_conflict(self):
        old = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
        draft = [{"id": "a", "name": "edited"}, old[1]]
        live = [old[0], {"id": "b", "name": "external"}]
        self.assertEqual(merge_settings_objects(old, draft, live), [draft[0], live[1]])
        with self.assertRaises(ValueError):
            merge_settings_objects(old, draft, [{"id": "a", "name": "conflict"}])

    def test_mcp_editor_is_embedded_and_cancel_keeps_saved_config(self):
        page = self.page("capabilities", "MCP 服务")
        manager = page.mcp_server_manager
        original = self.config.get_mcp_servers()
        with patch("main.QDialog.exec", side_effect=AssertionError("modal editor")):
            manager.add_server()
        self.assertIsNotNone(manager.editor_dialog)
        with patch("main.ProductMessageDialog.exec_result", side_effect=AssertionError("unchanged leave prompt")):
            manager.editor_dialog.back_button.click()
        self.assertIsNone(manager.editor_dialog)
        self.assertEqual(self.config.get_mcp_servers(), original)

    def test_mcp_editor_transport_matches_imported_config(self):
        for server in (None, {"type": "streamable-http", "url": "https://example.com/mcp"},
                       {"url": "https://example.com/mcp"},
                       {"transport": "stdio", "url": "https://example.com/mcp"}):
            with self.subTest(server=server):
                editor = McpServerEditDialog(server)
                try:
                    self.assertEqual(editor.transport_combo.currentData(), "streamable_http")
                    self.assertEqual(editor.transport_stack.currentIndex(), 1)
                finally:
                    editor.deleteLater()
        editor = McpServerEditDialog({"command": "npx"})
        self.assertEqual(editor.transport_combo.currentData(), "stdio")
        self.assertEqual(editor.transport_stack.currentIndex(), 0)
        editor.deleteLater()

    def test_mcp_add_import_opens_remote_form_and_saves_only_on_save(self):
        page = self.page("capabilities", "MCP 服务")
        manager = page.mcp_server_manager
        original = self.config.get_mcp_servers()
        manager.add_server()
        self.assertFalse(manager.editor_dialog.import_button.isHidden())
        payload = '{"mcpServers":{"remote":{"url":"https://example.com/mcp","headers":{"X-Test":"value"}}}}'
        with patch("main.McpJsonImportDialog.exec", return_value=QDialog.Accepted), \
                patch("main.McpJsonImportDialog.json_text", return_value=payload), \
                patch("main.QMessageBox.information"):
            manager.editor_dialog.import_button.click()
        editor = manager.editor_dialog
        self.assertEqual(editor.name_input.text(), "remote")
        self.assertEqual(editor.transport_stack.currentIndex(), 1)
        self.assertEqual(editor.url_input.text(), "https://example.com/mcp")
        self.assertIn("X-Test: value", editor.headers_edit.toPlainText())
        self.assertEqual(self.config.get_mcp_servers(), original)
        self.assertTrue(page._settings_dirty)
        editor.accept()
        self.assertIsNone(manager.editor_dialog)
        saved = next(s for s in self.config.get_mcp_servers() if s["id"] == "remote")
        self.assertEqual(saved["transport"], "streamable_http")
        self.assertEqual(saved["headers"], {"X-Test": "value"})

    def test_mcp_import_cancel_or_invalid_json_keeps_form_input(self):
        page = self.page("capabilities", "MCP 服务")
        manager = page.mcp_server_manager
        manager.add_server()
        editor = manager.editor_dialog
        editor.name_input.setText("正在填写的服务")
        with patch("main.McpJsonImportDialog.exec", side_effect=[QDialog.Accepted, QDialog.Rejected]) as execute, \
                patch("main.McpJsonImportDialog.json_text", return_value="{broken"), \
                patch("main.QMessageBox.warning") as warning:
            editor.import_button.click()
        self.assertEqual(execute.call_count, 2)
        warning.assert_called_once()
        self.assertIs(manager.editor_dialog, editor)
        self.assertEqual(editor.name_input.text(), "正在填写的服务")
        self.assertEqual(manager.servers, [])
        with patch("main.McpJsonImportDialog.exec", return_value=QDialog.Accepted), \
                patch("main.McpJsonImportDialog.json_text", return_value='{"url":"https://example.com/mcp"}'), \
                patch("main.ProductMessageDialog.exec_result", return_value="stay"):
            editor.import_button.click()
        self.assertIs(manager.editor_dialog, editor)
        self.assertEqual(manager.servers, [])

    def test_mcp_list_import_keeps_duplicate_choices_and_supports_mixed_services(self):
        page = self.page("capabilities", "MCP 服务")
        manager = page.mcp_server_manager
        manager.servers = [{"id": "remote", "name": "remote", "transport": "streamable_http", "url": "https://old.example/mcp"}]
        payload = '{"mcpServers":{"remote":{"url":"https://new.example/mcp"},"local":{"command":"npx","args":["example"]}}}'
        with patch("main.McpJsonImportDialog.exec", return_value=QDialog.Accepted), \
                patch("main.McpJsonImportDialog.json_text", return_value=payload), \
                patch("main.QMessageBox.question", return_value=QMessageBox.Yes), \
                patch("main.QMessageBox.information"):
            manager.import_servers_from_json()
        self.assertIsNone(manager.editor_dialog)
        self.assertEqual([s["transport"] for s in manager.servers], ["streamable_http", "stdio"])
        self.assertEqual(manager.servers[0]["url"], "https://new.example/mcp")


if __name__ == "__main__":
    unittest.main()
