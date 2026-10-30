"""Regression checks for inline editing and tool-file navigation."""

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from main import ChatBubble, MainWindow, ToolCallCard


class ConversationFileNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if os.name == "nt":
            for name in ("msyh.ttc", "msyhbd.ttc"):
                QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows"), "Fonts", name)))

    def settle(self):
        for _ in range(6):
            self.app.processEvents()

    def test_short_message_edit_expands_reflows_and_cancel_restores(self):
        bubble = ChatBubble("User", "你好", source_message_id="original")
        submitted = []
        bubble.editSubmitRequested.connect(lambda *args: submitted.append(args))
        try:
            bubble.apply_dynamic_widths(880, 640)
            bubble.show()
            self.settle()
            normal_width = bubble.user_content_edit.width()
            bubble.begin_inline_edit()
            text = r"针对D:\期货\腾讯全球大会\20261030这里的文档"
            bubble.user_content_edit.setPlainText(text)
            self.settle()
            self.assertGreater(bubble.user_content_edit.width(), normal_width * 3)
            self.assertLessEqual(bubble.user_content_edit.document().firstBlock().layout().lineCount(), 2)
            for width in (260, 640):
                # A hidden conversation must retain its editing width and input.
                bubble.hide()
                bubble.apply_dynamic_widths(width + 40, width)
                bubble.resize(width + 40, bubble.sizeHint().height())
                bubble.show()
                self.settle()
                editor = bubble.user_content_edit
                frame = bubble.user_bubble_frame
                self.assertLessEqual(frame.width(), width)
                self.assertLessEqual(abs(editor.geometry().left() - (frame.width() - editor.geometry().right() - 1)), 2)
                self.assertEqual(editor.toPlainText(), text)
                for button in bubble.edit_controls.findChildren(QPushButton):
                    self.assertTrue(bubble.edit_controls.rect().contains(button.geometry()))
            QTest.keyClick(bubble.user_content_edit, Qt.Key_Return, Qt.ControlModifier)
            self.assertEqual(submitted, [("original", text)])
            QTest.keyClick(bubble.user_content_edit, Qt.Key_Escape)
            self.settle()
            self.assertEqual(bubble.user_content_edit.toPlainText(), "你好")
            self.assertEqual(bubble.user_content_edit.width(), normal_width)
            self.assertTrue(bubble.user_content_edit.isReadOnly())
        finally:
            bubble.close()
            bubble.deleteLater()

    def test_tool_paths_are_individual_links_and_preserve_exact_arguments(self):
        special_path = "会议纪要/AI & WorkBuddy #1.md"
        cases = [
            ("text_file_read", {"path": special_path}, [special_path]),
            ("document_read", json.dumps({"file_path": special_path}), [special_path]),
            ("workspace_delete_path", {"path": special_path}, [special_path]),
            ("workspace_rename_path", {"old_path": "旧.md", "new_path": "新.md"}, ["旧.md", "新.md"]),
            ("apply_patch", {"patch": "*** Begin Patch\n*** Update File: old.md\n*** Move to: new.md\n@@\n-a\n+b\n*** Delete File: gone.md\n*** End Patch"}, ["old.md", "new.md", "gone.md"]),
            ("run_command", {"command": "echo D:/not-a-link.md"}, []),
        ]
        for name, args, paths in cases:
            with self.subTest(tool=name):
                card = ToolCallCard(name, args, "tool-id")
                activated, details = [], []
                card.filePathActivated.connect(activated.append)
                card.clicked.connect(lambda *values: details.append(values))
                try:
                    for index in range(len(paths)):
                        card.tool_context_label.linkActivated.emit(f"tool-file:{index}")
                    card.tool_context_label.linkActivated.emit("file:///wrong.md")
                    self.assertEqual(activated, paths)
                    self.assertEqual(card.args, args)
                    self.assertEqual(details, [])
                    card.view_btn.click()
                    self.assertEqual(details[0][0], "tool-id")
                    if paths:
                        self.assertIn("text-decoration: underline", card.tool_context_label.text())
                    if special_path in paths:
                        self.assertIn("&amp;", card.tool_context_label.text())
                finally:
                    card.deleteLater()

    def test_navigation_uses_source_workspace_and_keeps_missing_files_and_other_sessions_isolated(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "会议纪要.md")
            path.write_text("# 会议纪要", encoding="utf-8")
            state = SimpleNamespace(session_id="source", persisted_conversation_meta={})
            window = SimpleNamespace(
                current_session_id="source", get_session=lambda sid: state if sid == "source" else None,
                _workspace_dir_for_state=lambda value: temporary,
                show_context_drawer=MagicMock(), select_deliverable=MagicMock(return_value=True),
                set_file_navigator_scope=MagicMock(), set_file_navigator_visible=MagicMock(),
                register_deliverable_paths=MagicMock(), add_system_toast=MagicMock(),
                RIGHT_TAB_FILES=MainWindow.RIGHT_TAB_FILES, FILE_SCOPE_WORKSPACE=MainWindow.FILE_SCOPE_WORKSPACE,
            )
            self.assertTrue(MainWindow.open_tool_file_from_chat(window, path.name, "source", "read"))
            window.select_deliverable.assert_called_once_with(str(path), render_html=True)
            window.show_context_drawer.assert_called_once_with(window.RIGHT_TAB_FILES)
            window.register_deliverable_paths.assert_not_called()
            window.select_deliverable.reset_mock()
            window.show_context_drawer.reset_mock()
            window.current_session_id = "other"
            self.assertFalse(MainWindow.open_tool_file_from_chat(window, path.name, "source", "read"))
            window.current_session_id = "source"
            path.unlink()
            self.assertFalse(MainWindow.open_tool_file_from_chat(window, path.name, "source", "delete"))
            window.select_deliverable.assert_not_called()
            window.show_context_drawer.assert_not_called()
            self.assertIn("文件已删除", window.add_system_toast.call_args.args[0])
            path.write_text("# 恢复", encoding="utf-8")
            state.persisted_conversation_meta = {"branch_file_map": {"old.md": str(path)}}
            window.select_deliverable.return_value = False
            window.set_file_navigator_scope.reset_mock()
            self.assertFalse(MainWindow.open_tool_file_from_chat(window, "old.md", "source", "edit"))
            window.select_deliverable.assert_called_once_with(str(path), render_html=True)
            window.set_file_navigator_scope.assert_not_called()


if __name__ == "__main__":
    unittest.main()
