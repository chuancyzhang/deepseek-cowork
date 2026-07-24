import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtGui import QHelpEvent, QImage
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QToolButton, QWidget

from core.chat_storage import ChatStorage
from core.config_manager import ConfigManager
from main import (
    AutoResizingInputEdit,
    CapabilityWorkbenchDialog,
    FeishuQrDialog,
    FileChip,
    MainWindow,
    QMessageBox,
    SettingsDialog,
)
from ui.primitives import ProductTooltipController


class ProductExperienceFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_dirty_state_ignores_background_logs_and_reverts(self):
        dialog = SettingsDialog(ConfigManager())
        try:
            self.assertFalse(dialog._settings_dirty)
            original = dialog.default_ws_input.text()
            dialog.update_log_edit.append("background update progress")
            self.app.processEvents()
            self.assertFalse(dialog._settings_dirty)

            dialog.default_ws_input.setText(original + "-changed")
            self.app.processEvents()
            self.assertTrue(dialog._settings_dirty)
            self.assertTrue(dialog.save_settings_btn.isEnabled())

            dialog.default_ws_input.setText(original)
            self.app.processEvents()
            self.assertFalse(dialog._settings_dirty)
            self.assertFalse(dialog.save_settings_btn.isEnabled())
        finally:
            dialog._allow_close_without_prompt = True
            dialog.close()

    def test_enterprise_message_has_local_save_boundary_and_single_channel(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir", return_value=temp_dir
        ), patch("core.config_manager.get_base_dir", return_value=temp_dir):
            config = ConfigManager()
            config.set(
                "im_gateway",
                {
                    "enabled_providers": ["feishu"],
                    "providers": {
                        "feishu": {
                            "enabled": True,
                            "app_id": "cli-a",
                            "app_secret": "secret-a",
                        },
                        "dingtalk": {"enabled": False},
                        "wecom": {"enabled": False},
                    },
                },
            )
            host = QWidget()
            host.gateway_process = None
            host.stop_gateway_process = MagicMock()
            host.queue_daemon_connection = MagicMock()
            host.start_gateway_process = MagicMock(return_value=True)
            with patch("main.write_im_gateway_status"), patch(
                "main.read_im_gateway_status",
                return_value={},
            ):
                dialog = SettingsDialog(config, parent=host, initial_page_label="企业消息")
                try:
                    dialog._select_im_provider("wecom")
                    dialog.wecom_fields["bot_id"].setText("bot-a")
                    dialog.wecom_fields["secret"].setText("secret-b")
                    self.app.processEvents()
                    self.assertFalse(dialog._settings_dirty)
                    dialog._connect_form_im_provider("wecom")
                    saved = config.get("im_gateway")
                    self.assertEqual(saved["enabled_providers"], ["wecom"])
                    self.assertEqual(saved["providers"]["wecom"]["bot_id"], "bot-a")
                    self.assertEqual(saved["providers"]["feishu"]["app_id"], "cli-a")
                    self.assertFalse(saved["providers"]["feishu"]["enabled"])
                    host.stop_gateway_process.assert_called_once()
                    host.start_gateway_process.assert_called_once()
                finally:
                    dialog._allow_close_without_prompt = True
                    dialog.close()

    def test_feishu_qr_dialog_renders_local_qr_and_countdown(self):
        with patch.object(FeishuQrDialog, "_start_worker", new=lambda self: None):
            dialog = FeishuQrDialog()
        try:
            dialog._on_qr_ready("https://accounts.feishu.cn/device?code=test", 120)
            self.app.processEvents()
            self.assertFalse(dialog.qr_label.pixmap().isNull())
            self.assertTrue(dialog.open_link_btn.isEnabled())
            self.assertIn("秒", dialog.status_notice.label.text())
            dialog._refresh_theme()
            self.assertFalse(dialog.qr_label.pixmap().isNull())
        finally:
            dialog._timer.stop()
            dialog.close()

    def test_model_edit_delete_channel_and_empty_save_are_semantic_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir", return_value=temp_dir
        ), patch("core.config_manager.get_base_dir", return_value=temp_dir):
            config = ConfigManager()
            dialog = SettingsDialog(config)
            try:
                editor = dialog.model_channel_manager.editors[0]
                current = dict(editor._models()[0])
                edited = dict(current)
                edited["display_name"] = "Edited Model"
                with patch("main.ModelEditDialog") as dialog_type:
                    model_dialog = dialog_type.return_value
                    model_dialog.exec.return_value = QDialog.Accepted
                    model_dialog.get_model.return_value = edited
                    editor.edit_model()
                self.app.processEvents()
                self.assertTrue(dialog._settings_dirty)
                self.assertTrue(dialog.save_settings_btn.isEnabled())

                dialog._clear_settings_dirty()
                editor.test_status_label.setText("测试通过 · 0.2 秒")
                self.app.processEvents()
                self.assertFalse(dialog._settings_dirty)

                with patch("main.QMessageBox.question", return_value=QMessageBox.Yes):
                    while dialog.model_channel_manager.editors:
                        dialog.model_channel_manager.delete_selected_channel()
                self.app.processEvents()
                self.assertEqual(dialog.model_channel_manager.get_channels(), [])
                self.assertTrue(dialog._settings_dirty)
                self.assertFalse(dialog.model_channel_manager.delete_selected_channel_btn.isEnabled())

                dialog.save_settings()
                self.assertEqual(config.get_model_channels(), [])
                self.assertEqual(config.get_selected_model_id(), "")
                self.assertFalse(dialog._settings_dirty)
            finally:
                dialog._allow_close_without_prompt = True
                dialog.close()

    def test_skill_config_save_auto_enables_mcp_and_keeps_separate_test_action(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.config_manager.get_app_data_dir", return_value=temp_dir
        ), patch("core.config_manager.get_base_dir", return_value=temp_dir):
            config = ConfigManager()
            manager = MagicMock()
            manager.is_skill_editable.return_value = False
            manager.list_skill_files.return_value = {"ok": False, "error": "read only"}
            manager.get_tool_record.return_value = None
            manager.get_skill_config_status.return_value = {
                "missing_required": [],
                "config_errors": [],
                "complete": True,
            }
            manager.build_skill_mcp_server_configs.return_value = {
                "ok": True,
                "error": "",
                "servers": [
                    {
                        "id": "superset-mcp",
                        "name": "Superset MCP",
                        "enabled": True,
                        "transport": "streamable_http",
                        "url": "https://superset.example/mcp",
                        "source_skill": "superset-mcp",
                        "managed_by_skill": True,
                    }
                ],
            }
            skill = {
                "name": "superset-mcp",
                "display_name": "Superset MCP",
                "config_fields": [
                    {"name": "SUPERSET_MCP_URL", "label": "Superset MCP URL", "required": True}
                ],
                "mcp_server_presets": [{"id": "superset-mcp", "name": "Superset MCP"}],
                "tools": [],
                "script_entries": [],
            }
            dialog = CapabilityWorkbenchDialog(skill, manager, config)
            try:
                dialog.config_editors["SUPERSET_MCP_URL"].setText("https://superset.example/mcp")
                button_texts = {button.text() for button in dialog.findChildren(QPushButton)}
                self.assertIn("保存配置", button_texts)
                self.assertIn("测试连接", button_texts)
                self.assertNotIn("生成 / 更新 MCP 配置", button_texts)

                with patch("main.QMessageBox.information"), patch.object(config, "_write_config") as write_config:
                    dialog.save_skill_config()

                server = config.get_mcp_servers()[0]
                self.assertEqual(write_config.call_count, 1)
                self.assertTrue(server["enabled"])
                self.assertEqual(server["source_skill"], "superset-mcp")
                self.assertIn("连接待测试", dialog.managed_mcp_status.text())
            finally:
                dialog.close()

    def test_deliverable_registry_only_returns_registered_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "history.sqlite"))
            workspace = os.path.join(temp_dir, "workspace")
            os.makedirs(workspace)
            result_path = os.path.join(workspace, "result.html")
            source_path = os.path.join(workspace, "notes.md")
            with open(result_path, "w", encoding="utf-8") as handle:
                handle.write("<html></html>")
            with open(source_path, "w", encoding="utf-8") as handle:
                handle.write("# source")

            storage.register_deliverable(
                workspace, result_path, conversation_id="not-yet-persisted", source="generated"
            )
            records = storage.list_deliverables(workspace)

            self.assertEqual([item["path"] for item in records], [result_path])
            self.assertIsNone(records[0]["conversation_id"])
            self.assertTrue(storage.is_deliverable(workspace, result_path))
            self.assertFalse(storage.is_deliverable(workspace, source_path))
            self.assertTrue(storage.unregister_deliverable(workspace, result_path))
            self.assertEqual(storage.list_deliverables(workspace), [])

    def test_product_tooltip_is_a_child_surface(self):
        host = QWidget()
        host.resize(320, 180)
        button = QPushButton("Target", host)
        button.setToolTip("Readable tooltip")
        host.show()
        self.app.processEvents()
        controller = ProductTooltipController(self.app)
        event = QHelpEvent(QHelpEvent.ToolTip, QPoint(4, 4), button.mapToGlobal(QPoint(4, 4)))
        controller.show_for_event(button, event, button.toolTip())
        self.app.processEvents()
        try:
            self.assertTrue(controller.bubble.isVisible())
            self.assertFalse(controller.bubble.isWindow())
            self.assertEqual(controller.bubble.parentWidget(), host)
            self.assertEqual(controller.bubble.text(), "Readable tooltip")
        finally:
            controller.hide()
            host.close()
            controller.dispose()

    def test_input_paste_routes_clipboard_image_to_window(self):
        host = QWidget()
        captured = []
        host._add_clipboard_image = captured.append
        editor = AutoResizingInputEdit(host)
        mime = QMimeData()
        image = QImage(12, 8, QImage.Format_ARGB32)
        image.fill(Qt.red)
        mime.setImageData(image)

        editor.insertFromMimeData(mime)

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].size(), image.size())

    def test_input_height_animation_is_owned_by_editor(self):
        host = QWidget()
        editor = AutoResizingInputEdit(host)
        editor.setFixedHeight(120)
        editor.setPlainText("触发输入框高度调整")

        editor.adjustHeight()

        self.assertIsNotNone(editor.anim)
        self.assertIs(editor.anim.parent(), editor)

    def test_clipboard_image_is_saved_in_session_attachment_directory(self):
        class Stub:
            _managed_attachment_root = MainWindow._managed_attachment_root
            _session_attachment_dir = MainWindow._session_attachment_dir
            _add_clipboard_image = MainWindow._add_clipboard_image

        with tempfile.TemporaryDirectory() as temp_dir:
            stub = Stub()
            stub.chat_history_dir = temp_dir
            state = SimpleNamespace(session_id="session-1")
            stub.get_current_session = lambda: state
            added = []
            stub._add_prompt_files = lambda paths: added.extend(paths)
            stub.add_system_toast = MagicMock()
            image = QImage(18, 12, QImage.Format_ARGB32)
            image.fill(Qt.green)

            path = stub._add_clipboard_image(image)

            self.assertTrue(os.path.isfile(path))
            self.assertEqual(added, [path])
            self.assertIn(os.path.join("attachments", "session-1"), path)

    def test_image_file_chip_renders_thumbnail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "shot.png")
            image = QImage(20, 10, QImage.Format_ARGB32)
            image.fill(Qt.blue)
            self.assertTrue(image.save(path))
            chip = FileChip(path, removable=True)
            try:
                thumbnail = next(label for label in chip.findChildren(QLabel) if label.pixmap() is not None)
                self.assertGreaterEqual(chip.sizeHint().height(), 40)
                self.assertEqual(thumbnail.size(), thumbnail.maximumSize())
            finally:
                chip.deleteLater()

    def test_vision_preflight_blocks_unsupported_model(self):
        class Stub:
            _ensure_vision_attachment_support = MainWindow._ensure_vision_attachment_support

            def _normalize_prompt_file_paths(self, paths):
                return list(paths)

            def _is_supported_image_attachment(self, path):
                return path.endswith(".png")

            def _selected_model_supports_vision(self, state):
                return False

            def _model_id_for_state(self, state):
                return "text-only"

        stub = Stub()
        stub.current_session_id = "session-1"
        stub.add_system_toast = MagicMock()
        stub.model_select_btn = QToolButton()
        stub.model_select_btn.showMenu = MagicMock()
        state = SimpleNamespace(session_id="session-1")

        result = stub._ensure_vision_attachment_support(state, ["shot.png"])

        self.assertFalse(result)
        stub.add_system_toast.assert_called_once()

    def test_system_toasts_stack_queue_and_deduplicate_without_session(self):
        class Stub:
            _system_toast_duration = MainWindow._system_toast_duration
            _system_toast_target_position = MainWindow._system_toast_target_position
            _position_active_system_toast = MainWindow._position_active_system_toast
            _show_next_system_toast = MainWindow._show_next_system_toast
            _dismiss_system_toast = MainWindow._dismiss_system_toast
            add_system_toast = MainWindow.add_system_toast

        stub = Stub()
        stub.main_container = QWidget()
        stub.main_container.resize(900, 600)
        stub._system_toast_queue = []
        stub._visible_system_toasts = []
        stub._active_system_toast = None

        stub.add_system_toast("已保存", "success", auto_close_ms=0)
        stub.add_system_toast("已保存", "success", auto_close_ms=0)
        stub.add_system_toast("第二条", "info", auto_close_ms=0)
        stub.add_system_toast("第三条", "warning", auto_close_ms=0)
        stub.add_system_toast("第四条", "error", auto_close_ms=0)
        self.app.processEvents()

        self.assertEqual(len(stub._visible_system_toasts), 3)
        self.assertEqual(len(stub._system_toast_queue), 1)
        self.assertEqual(stub._visible_system_toasts[0].repeat_count, 2)
        positions = [toast.pos().y() for toast in stub._visible_system_toasts]
        self.assertLess(positions[0], positions[1])
        self.assertLess(positions[1], positions[2])

        stub._dismiss_system_toast(stub._visible_system_toasts[-1])
        self.app.processEvents()
        self.assertEqual(len(stub._visible_system_toasts), 3)
        self.assertEqual(len(stub._system_toast_queue), 0)
        for toast in list(stub._visible_system_toasts):
            stub._dismiss_system_toast(toast)


if __name__ == "__main__":
    unittest.main()
