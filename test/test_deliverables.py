import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget, QTextEdit, QWidget

from main import (
    EmptyStateWidget,
    MainWindow,
    deliverable_preview_bootstrap_script,
    deliverable_preview_settle_script,
    scan_workspace_deliverables,
)


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

    def test_conversion_creates_project_conversation_from_current_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Report</body></html>")

            window = MainWindow.__new__(MainWindow)
            state = type("_Session", (), {"session_id": "generated-session"})()
            window.current_deliverable_path = html_path
            window.workspace_dir = tmp
            window._workspace_dir_for_state = MagicMock(return_value=tmp)
            window.get_session = MagicMock(return_value=state)
            window.create_new_session = MagicMock(return_value="generated-session")
            window._set_prompt_files = MagicMock()
            window._submit_session_request = MagicMock(return_value=True)
            window.add_system_toast = MagicMock()

            window.start_deliverable_conversion("pptx")

            window.create_new_session.assert_called_once_with(
                title="基于 HTML 生成 PPTX",
                make_current=True,
                workspace_dir=tmp,
            )
            window.get_session.assert_called_once_with("generated-session")
            self.assertEqual(state.selected_deliverable_path, html_path)
            window._set_prompt_files.assert_called_once_with(
                [html_path], session_id="generated-session", refresh=True
            )
            submit_call = window._submit_session_request.call_args
            self.assertIs(submit_call.args[0], state)
            self.assertIn("生成 PPTX 办公文件", submit_call.args[1])
            self.assertEqual(submit_call.args[2], [html_path])
            self.assertFalse(submit_call.kwargs["check_duplicates"])
            self.assertTrue(submit_call.kwargs["clear_current_input"])
            window.add_system_toast.assert_called_once_with(
                "已创建普通对话，开始生成 PPTX",
                "info",
                session_id="generated-session",
                auto_close_ms=3200,
            )

    def test_conversion_keeps_new_conversation_when_submission_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Report</body></html>")

            window = MainWindow.__new__(MainWindow)
            state = type("_Session", (), {"session_id": "generated-session"})()
            window.current_deliverable_path = html_path
            window._workspace_dir_for_state = MagicMock(return_value=tmp)
            window.create_new_session = MagicMock(return_value="generated-session")
            window.get_session = MagicMock(return_value=state)
            window._set_prompt_files = MagicMock()
            window._submit_session_request = MagicMock(return_value=False)
            window.add_system_toast = MagicMock()

            window.start_deliverable_conversion("pdf")

            self.assertEqual(state.selected_deliverable_path, html_path)
            self.assertIn("HTML 已保留在新对话中", window.add_system_toast.call_args.args[0])
            self.assertEqual(window.add_system_toast.call_args.args[1], "warning")

    def test_render_uses_cache_busting_local_url(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Report</body></html>")

            window = MainWindow.__new__(MainWindow)
            window.current_deliverable_path = html_path
            window.current_deliverable_stale = True
            window.deliverable_web_view = QWidget()
            window.deliverable_web_view.setUrl = MagicMock()
            window.deliverable_text_preview = QTextEdit()
            window.deliverable_preview_stack = QStackedWidget()
            window.deliverable_preview_stack.addWidget(window.deliverable_text_preview)
            window.deliverable_preview_stack.addWidget(window.deliverable_web_view)
            window.deliverable_status_label = QLabel()

            window.render_selected_deliverable()

            rendered_url = window.deliverable_web_view.setUrl.call_args.args[0]
            self.assertTrue(rendered_url.isLocalFile())
            self.assertEqual(os.path.normcase(rendered_url.toLocalFile()), os.path.normcase(html_path))
            self.assertIn("cowork_refresh=", rendered_url.query())
            self.assertFalse(window.current_deliverable_stale)
            self.assertIs(window.deliverable_preview_stack.currentWidget(), window.deliverable_web_view)

    def test_render_reuses_unchanged_html(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Report</body></html>")

            window = MainWindow.__new__(MainWindow)
            window.current_deliverable_path = html_path
            window.current_deliverable_stale = False
            window.deliverable_render_path = html_path
            window.deliverable_render_fingerprint = window._deliverable_fingerprint(html_path)
            window.deliverable_web_view = QWidget()
            window.deliverable_web_view.setUrl = MagicMock()
            window.deliverable_text_preview = QTextEdit()
            window.deliverable_preview_stack = QStackedWidget()
            window.deliverable_preview_stack.addWidget(window.deliverable_text_preview)
            window.deliverable_preview_stack.addWidget(window.deliverable_web_view)
            window.deliverable_status_label = QLabel()

            window.render_selected_deliverable()

            window.deliverable_web_view.setUrl.assert_not_called()
            self.assertIs(window.deliverable_preview_stack.currentWidget(), window.deliverable_web_view)

    def test_light_preview_scripts_throttle_continuous_rendering(self):
        bootstrap = deliverable_preview_bootstrap_script()
        settle = deliverable_preview_settle_script()

        self.assertIn("requestAnimationFrame", bootstrap)
        self.assertIn("Math.max(100", bootstrap)
        self.assertIn("animation:none", bootstrap)
        self.assertIn("MutationObserver", bootstrap)
        self.assertIn("getAnimations", settle)
        self.assertIn("media.pause", settle)


if __name__ == "__main__":
    unittest.main()
