import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sop_manager import create_sop_run, mark_step_awaiting_confirmation
from main import MainWindow


class _State:
    def __init__(self, sop_run=None):
        self.sop_run = sop_run


class TestSopUiHelpers(unittest.TestCase):
    def test_prompt_tool_menu_order_helper_matches_spec(self):
        window = MainWindow.__new__(MainWindow)
        entries = window._prompt_tool_menu_entries()
        self.assertEqual(
            [label for _key, label in entries],
            ["添加文件", "添加智能体", "添加 SOP", "指定能力", "反问模式"],
        )

    def test_should_block_send_for_sop_only_when_awaiting_confirmation(self):
        window = MainWindow.__new__(MainWindow)
        active_run = create_sop_run(
            {
                "id": "office",
                "name": "Office",
                "steps": [{"title": "Step 1"}],
            }
        )
        awaiting_run = mark_step_awaiting_confirmation(active_run, {"finished_at": 1})

        self.assertFalse(window._should_block_send_for_sop(_State(active_run)))
        self.assertTrue(window._should_block_send_for_sop(_State(awaiting_run)))

    def test_add_prompt_files_loads_workspace_and_tracks_attachments(self):
        class _InputField:
            def __init__(self, text=""):
                self._text = text
                self.focused = False
                self.cursor_moved = False
                self.cursor_visible = False
                self.height_adjusted = False

            def toPlainText(self):
                return self._text

            def setPlainText(self, text):
                self._text = text

            def moveCursor(self, *_args):
                self.cursor_moved = True

            def ensureCursorVisible(self):
                self.cursor_visible = True

            def setFocus(self):
                self.focused = True

            def adjustHeight(self):
                self.height_adjusted = True

        window = MainWindow.__new__(MainWindow)
        window.input_field = _InputField("已有说明")
        window.workspace_dir = None
        window.current_session_id = "session-1"
        window.sessions = {"session-1": type("_State", (), {"prompt_files": []})()}
        loaded_dirs = []
        hint_calls = []
        window.refresh_prompt_file_chips = MagicMock()
        window.load_workspace = lambda path: loaded_dirs.append(path)
        window.set_context_tab_hint = lambda tab, available=True: hint_calls.append((tab, available))

        with tempfile.TemporaryDirectory() as temp_dir:
            file_a = os.path.join(temp_dir, "a.txt")
            file_b = os.path.join(temp_dir, "b.txt")
            for path in (file_a, file_b):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("x")

            added = window._add_prompt_files([file_a, file_b])

        self.assertEqual(added, [os.path.normpath(file_a), os.path.normpath(file_b)])
        self.assertEqual(loaded_dirs, [temp_dir])
        self.assertEqual(window.input_field.toPlainText(), "已有说明")
        self.assertEqual(
            window.sessions["session-1"].prompt_files,
            [os.path.normpath(file_a), os.path.normpath(file_b)],
        )
        self.assertEqual(hint_calls, [(window.RIGHT_TAB_FILES, True)])
        window.refresh_prompt_file_chips.assert_called()
        self.assertTrue(window.input_field.focused)
        self.assertTrue(window.input_field.cursor_moved)
        self.assertTrue(window.input_field.cursor_visible)
        self.assertTrue(window.input_field.height_adjusted)

    def test_build_user_message_payload_marks_user_added_files(self):
        window = MainWindow.__new__(MainWindow)

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, "AI 赋能数据分析.docx")
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write("x")

            payload = window._build_user_message_payload("整理下这篇文章", [file_path])

        self.assertIn("[用户添加的文件]", payload["content"])
        self.assertIn("整理下这篇文章", payload["content"])
        self.assertEqual(payload["display_content"], "整理下这篇文章")
        self.assertEqual(payload["attachments"][0]["name"], "AI 赋能数据分析.docx")
        self.assertEqual(payload["meta"]["display_content"], "整理下这篇文章")
        self.assertEqual(payload["meta"]["user_added_files"], [os.path.normpath(file_path)])
        self.assertEqual(payload["content_parts"][0], {"type": "text", "text": "整理下这篇文章"})
        self.assertEqual(payload["content_parts"][1]["type"], "input_file")
        self.assertEqual(payload["content_parts"][1]["path"], os.path.normpath(file_path))

    def test_select_files_for_prompt_forwards_dialog_selection(self):
        window = MainWindow.__new__(MainWindow)
        window.workspace_dir = ""
        window.config_manager = MagicMock()
        window.config_manager.get.return_value = ""
        window._add_prompt_files = MagicMock()

        with patch("main.QFileDialog.getOpenFileNames", return_value=(["C:\\demo.txt"], "所有文件 (*.*)")):
            window.select_files_for_prompt()

        window._add_prompt_files.assert_called_once_with(["C:\\demo.txt"])


if __name__ == "__main__":
    unittest.main()
