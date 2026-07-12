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
from main import AutoResizingInputEdit, FileChip, MainWindow, QMessageBox, SettingsDialog
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
