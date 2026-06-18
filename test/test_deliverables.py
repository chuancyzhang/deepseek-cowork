import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from PySide6.QtWidgets import QApplication

from main import EmptyStateWidget, MainWindow, scan_workspace_deliverables


class TestDeliverableScanning(unittest.TestCase):
    def test_scans_supported_deliverables_sorted_by_modified_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_html = os.path.join(tmp, "report.html")
            new_pptx = os.path.join(tmp, "deck.pptx")
            ignored = os.path.join(tmp, "notes.txt")
            with open(old_html, "w", encoding="utf-8") as f:
                f.write("<!doctype html><html><body>Report</body></html>")
            with open(new_pptx, "wb") as f:
                f.write(b"pptx")
            with open(ignored, "w", encoding="utf-8") as f:
                f.write("ignore")
            now = time.time()
            os.utime(old_html, (now - 20, now - 20))
            os.utime(new_pptx, (now, now))

            items = scan_workspace_deliverables(tmp)

        self.assertEqual([item["name"] for item in items], ["deck.pptx", "report.html"])
        self.assertEqual(items[0]["kind"], "pptx")
        self.assertEqual(items[1]["kind"], "html")

    def test_skips_cache_and_build_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            visible = os.path.join(tmp, "page.html")
            hidden_dir = os.path.join(tmp, ".git")
            build_dir = os.path.join(tmp, "build")
            os.makedirs(hidden_dir)
            os.makedirs(build_dir)
            with open(visible, "w", encoding="utf-8") as f:
                f.write("<html></html>")
            with open(os.path.join(hidden_dir, "hidden.html"), "w", encoding="utf-8") as f:
                f.write("<html></html>")
            with open(os.path.join(build_dir, "built.html"), "w", encoding="utf-8") as f:
                f.write("<html></html>")

            items = scan_workspace_deliverables(tmp)

        self.assertEqual([item["name"] for item in items], ["page.html"])

    def test_empty_state_replaces_report_card_with_html_deliverable_card(self):
        app = QApplication.instance() or QApplication([])
        class PromptBox:
            def setText(self, text):
                self.text = text

        class MainWindowStub:
            def __init__(self):
                self.input_field = PromptBox()

        main_window = MainWindowStub()
        widget = EmptyStateWidget(main_window)
        try:
            titles = [item[0] for item in widget.actions_data]
            self.assertEqual(len(titles), 4)
            self.assertIn("生成 HTML 交付物", titles)
            self.assertNotIn("生成报告", titles)
            html_card = next(item for item in widget.actions_data if item[0] == "生成 HTML 交付物")
            self.assertEqual(html_card[1], "预览修改，再生成 PPT")
            self.assertIn("右侧交付物", html_card[2])
            self.assertIn("生成 PPTX", html_card[2])
            self.assertEqual(html_card[3], "fa5s.file-code")
            widget.action_cards[titles.index("生成 HTML 交付物")].click()
            self.assertEqual(main_window.input_field.text, html_card[2])
        finally:
            widget.deleteLater()

    def test_conversion_continues_in_current_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Report</body></html>")

            window = MainWindow.__new__(MainWindow)
            state = type("_Session", (), {"session_id": "current-session"})()
            window.current_deliverable_path = html_path
            window.workspace_dir = tmp
            window._workspace_dir_for_state = MagicMock(return_value=tmp)
            window.get_current_session = MagicMock(return_value=state)
            window.get_session = MagicMock()
            window.create_new_session = MagicMock()
            window._set_prompt_files = MagicMock()
            window._submit_session_request = MagicMock(return_value=True)
            window.add_system_toast = MagicMock()

            window.start_deliverable_conversion("pptx")

            window.create_new_session.assert_not_called()
            window.get_session.assert_not_called()
            window._set_prompt_files.assert_called_once_with(
                [html_path], session_id="current-session", refresh=True
            )
            submit_call = window._submit_session_request.call_args
            self.assertIs(submit_call.args[0], state)
            self.assertIn("生成 PPTX 办公文件", submit_call.args[1])
            self.assertEqual(submit_call.args[2], [html_path])
            self.assertFalse(submit_call.kwargs["check_duplicates"])
            self.assertTrue(submit_call.kwargs["clear_current_input"])
            window.add_system_toast.assert_called_once_with(
                "已在当前对话中开始生成 PPTX",
                "info",
                session_id="current-session",
                auto_close_ms=3200,
            )


if __name__ == "__main__":
    unittest.main()
