import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from main import ConversationHistoryRow, MainWindow
from ui.primitives import ProductCodeViewer, ProductResultViewer, ProductSegmentedControl, SidebarInlineNameEditor


class MainWorkspaceLinearTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_observability_uses_shared_segments_and_structured_viewers(self):
        self.assertFalse(hasattr(self.window, "step_intro_label"))
        self.assertIsInstance(self.window.observability_segment_control, ProductSegmentedControl)
        self.assertIsInstance(self.window.td_args_edit, ProductCodeViewer)
        self.assertIsInstance(self.window.td_result_edit, ProductResultViewer)

    def test_project_preview_keeps_five_conversations_and_hidden_actions(self):
        with tempfile.TemporaryDirectory() as project_dir:
            self.window.history_rows = {}
            self.window.history_buttons = {}
            self.window.history_age_labels = {}
            self.window.history_activity_indicators = {}
            self.window.project_rows = {}
            self.window.project_buttons = {}
            self.window.history_inline_hosts = {}
            self.window.project_inline_hosts = {}
            sessions = [
                {"id": f"session-{index}", "title": f"对话 {index}", "updated_at": index, "pinned": False}
                for index in range(7)
            ]
            self.window.project_preview_paths.add(project_dir)
            row = self.window._make_project_row(
                {"path": project_dir, "name": "项目", "pinned": False}, sessions
            )
            self.assertEqual(len(row.findChildren(ConversationHistoryRow)), 5)
            actions = row.findChild(QWidget, "ProjectActions")
            self.assertIsNotNone(actions)
            self.assertTrue(actions.isHidden())
            disclosure = [button.text().strip() for button in row.findChildren(type(self.window.action_btn))]
            self.assertIn("展开显示", disclosure)
            row.deleteLater()

    def test_manual_title_wins_over_generated_title(self):
        state = SimpleNamespace(
            session_id="manual-title-session",
            messages=[{"role": "user", "content": "自动生成标题"}],
            persisted_conversation_meta={"manual_title": True},
        )
        with patch.object(
            self.window.chat_storage,
            "get_conversation_record",
            return_value={"title": "用户命名", "meta": {"manual_title": True}},
        ):
            self.assertEqual(self.window._resolved_session_title(state), "用户命名")

    def test_chat_workspace_header_never_uses_workspace_uuid(self):
        state = self.window.get_current_session()
        with patch.object(self.window, "_session_workspace_source", return_value="chat"), patch.object(
            self.window, "_workspace_dir_for_state", return_value=r"D:\work\38e12083c3d349bb942869b3517de7fd"
        ), patch.object(self.window, "_resolved_session_title", return_value="客户周报"):
            self.window.update_conversation_header()
        self.assertEqual(self.window.workspace_title_label.text(), "客户周报")

    def test_inline_editor_enter_commits_and_escape_cancels(self):
        committed = []
        editor = SidebarInlineNameEditor("  新名称  ")
        editor.commitRequested.connect(committed.append)
        editor._commit()
        self.assertEqual(committed, ["新名称"])

        cancelled = []
        editor = SidebarInlineNameEditor("原名称")
        editor.cancelRequested.connect(lambda: cancelled.append(True))
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent

        editor.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        self.assertEqual(cancelled, [True])


if __name__ == "__main__":
    unittest.main()
