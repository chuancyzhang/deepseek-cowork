import hashlib
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from PySide6.QtCore import QByteArray, QBuffer, QEventLoop, QIODevice, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget, QTextEdit, QWidget

from main import (
    EmptyStateWidget,
    DeliverableWebPreview,
    MainWindow,
    deliverable_preview_bootstrap_script,
    deliverable_preview_settle_script,
    load_qwebengine_view,
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

    def test_conversion_continues_in_current_conversation_for_all_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Report</body></html>")

            for target_format in ("pptx", "docx", "pdf"):
                with self.subTest(target_format=target_format):
                    window = MainWindow.__new__(MainWindow)
                    state = type("_Session", (), {"session_id": "current-session"})()
                    window.current_deliverable_path = html_path
                    window.workspace_dir = tmp
                    window._workspace_dir_for_state = MagicMock(return_value=tmp)
                    window.get_current_session = MagicMock(return_value=state)
                    window.create_new_session = MagicMock()
                    window._set_prompt_files = MagicMock()
                    window._submit_session_request = MagicMock(return_value=True)
                    window.add_system_toast = MagicMock()

                    window.start_deliverable_conversion(target_format)

                    window.create_new_session.assert_not_called()
                    self.assertEqual(state.selected_deliverable_path, html_path)
                    window._set_prompt_files.assert_called_once_with(
                        [html_path], session_id="current-session", refresh=True
                    )
                    submit_call = window._submit_session_request.call_args
                    self.assertIs(submit_call.args[0], state)
                    self.assertIn(f"生成 {target_format.upper()} 办公文件", submit_call.args[1])
                    self.assertEqual(submit_call.args[2], [html_path])
                    self.assertFalse(submit_call.kwargs["check_duplicates"])
                    self.assertTrue(submit_call.kwargs["clear_current_input"])
                    window.add_system_toast.assert_called_once_with(
                        f"已在当前对话中开始生成 {target_format.upper()}",
                        "info",
                        session_id="current-session",
                        auto_close_ms=3200,
                    )

    def test_chat_file_link_opens_deliverable_in_focus_mode(self):
        with tempfile.TemporaryDirectory() as workspace:
            path = os.path.join(workspace, "报告.doc")
            with open(path, "wb") as handle:
                handle.write(b"doc")
            window = MainWindow.__new__(MainWindow)
            state = type("_Session", (), {"selected_deliverable_path": ""})()
            window.get_session = MagicMock(return_value=state)
            window._workspace_dir_for_state = MagicMock(return_value=workspace)
            window._apply_deliverable_layout_mode = MagicMock()
            window.show_context_drawer = MagicMock()
            window.select_deliverable = MagicMock()
            window.add_system_toast = MagicMock()

            window.open_deliverable_from_chat(path, "session-1")

            self.assertEqual(state.selected_deliverable_path, os.path.normpath(path))
            window._apply_deliverable_layout_mode.assert_called_once_with("focus")
            window.show_context_drawer.assert_called_once_with(window.RIGHT_TAB_DELIVERABLES)
            window.select_deliverable.assert_called_once_with(os.path.normpath(path), render_html=True)
            window.add_system_toast.assert_not_called()

    def test_conversion_keeps_html_in_current_conversation_when_submission_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Report</body></html>")

            window = MainWindow.__new__(MainWindow)
            state = type("_Session", (), {"session_id": "current-session"})()
            window.current_deliverable_path = html_path
            window._workspace_dir_for_state = MagicMock(return_value=tmp)
            window.get_current_session = MagicMock(return_value=state)
            window.create_new_session = MagicMock()
            window._set_prompt_files = MagicMock()
            window._submit_session_request = MagicMock(return_value=False)
            window.add_system_toast = MagicMock()

            window.start_deliverable_conversion("pdf")

            window.create_new_session.assert_not_called()
            self.assertEqual(state.selected_deliverable_path, html_path)
            self.assertIn("HTML 已保留在当前对话中", window.add_system_toast.call_args.args[0])
            self.assertEqual(window.add_system_toast.call_args.args[1], "warning")

    def test_conversion_reports_when_current_conversation_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "report.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<html><body>Report</body></html>")

            window = MainWindow.__new__(MainWindow)
            window.current_deliverable_path = html_path
            window._workspace_dir_for_state = MagicMock(return_value=tmp)
            window.get_current_session = MagicMock(return_value=None)
            window._set_prompt_files = MagicMock()
            window._submit_session_request = MagicMock()
            window.add_system_toast = MagicMock()

            window.start_deliverable_conversion("docx")

            window._set_prompt_files.assert_not_called()
            window._submit_session_request.assert_not_called()
            window.add_system_toast.assert_called_once_with(
                "当前没有可继续的对话，请先新建对话。",
                "warning",
                auto_close_ms=3200,
            )

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
        self.assertIn("__coworkScrollWheelAt", bootstrap)
        self.assertIn("__coworkPreviewMetrics", bootstrap)
        self.assertNotIn("addEventListener('wheel'", bootstrap)
        self.assertIn("document.scrollingElement", bootstrap)
        self.assertIn("getAnimations", settle)
        self.assertIn("media.pause", settle)

    def test_light_preview_qt_input_and_scrollbars_scroll_both_axes(self):
        webengine_view_cls = load_qwebengine_view()
        if webengine_view_cls is None:
            self.skipTest("QtWebEngine is unavailable")
        app = QApplication.instance() or QApplication([])
        view = webengine_view_cls()
        view.resize(320, 240)
        window = MainWindow.__new__(MainWindow)
        window.deliverable_web_view = view
        window._configure_deliverable_web_view()
        preview = DeliverableWebPreview(view)
        preview.resize(320, 240)
        preview.show()

        loaded = []
        load_loop = QEventLoop()
        view.loadFinished.connect(lambda ok: (loaded.append(bool(ok)), load_loop.quit()))
        view.setHtml(
            "<!doctype html><html><body style='margin:0;width:1600px;height:1600px'>"
            "<div id='nested' style='position:absolute;left:0;top:0;width:100px;height:100px;overflow:auto'>"
            "<div style='width:400px;height:500px'>nested</div></div>preview</body></html>"
        )
        QTimer.singleShot(5000, load_loop.quit)
        load_loop.exec()
        self.assertEqual(loaded, [True])

        def evaluate(script):
            result = []
            loop = QEventLoop()
            view.page().runJavaScript(script, lambda value: (result.append(value), loop.quit()))
            QTimer.singleShot(5000, loop.quit)
            loop.exec()
            self.assertTrue(result, f"JavaScript callback timed out: {script}")
            return result[0]

        def render_digest():
            payload = QByteArray()
            buffer = QBuffer(payload)
            buffer.open(QIODevice.WriteOnly)
            self.assertTrue(view.grab().save(buffer, "PNG"))
            return hashlib.sha256(bytes(payload)).hexdigest()

        routing_target = QWidget()
        routing_target.setObjectName("UnrelatedWheelTarget")
        outside_wheel = QWheelEvent(
            QPointF(10, 10),
            QPointF(view.mapToGlobal(QPoint(-20, -20))),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(routing_target, outside_wheel)
        QApplication.processEvents()
        self.assertEqual(evaluate("document.scrollingElement.scrollTop"), 0)
        control_wheel = QWheelEvent(
            QPointF(120, 100),
            QPointF(view.mapToGlobal(QPoint(120, 100))),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.NoButton,
            Qt.ControlModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(routing_target, control_wheel)
        QApplication.processEvents()
        self.assertEqual(evaluate("document.scrollingElement.scrollTop"), 0)
        before_wheel_digest = render_digest()
        wheel = QWheelEvent(
            QPointF(120, 100),
            QPointF(view.mapToGlobal(QPoint(120, 100))),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(routing_target, wheel)
        QApplication.processEvents()
        wheel_loop = QEventLoop()
        QTimer.singleShot(100, wheel_loop.quit)
        wheel_loop.exec()
        self.assertGreater(evaluate("document.scrollingElement.scrollTop"), 0)
        paint_loop = QEventLoop()
        QTimer.singleShot(200, paint_loop.quit)
        paint_loop.exec()
        self.assertNotEqual(render_digest(), before_wheel_digest)
        evaluate("window.scrollTo(0,0)")
        horizontal_wheel = QWheelEvent(
            QPointF(120, 100),
            QPointF(view.mapToGlobal(QPoint(120, 100))),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.NoButton,
            Qt.ShiftModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(routing_target, horizontal_wheel)
        QApplication.processEvents()
        horizontal_loop = QEventLoop()
        QTimer.singleShot(100, horizontal_loop.quit)
        horizontal_loop.exec()
        self.assertGreater(evaluate("document.scrollingElement.scrollLeft"), 0)
        evaluate("window.scrollTo(0,0)")
        nested_wheel = QWheelEvent(
            QPointF(50, 50),
            QPointF(view.mapToGlobal(QPoint(50, 50))),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(routing_target, nested_wheel)
        QApplication.processEvents()
        self.assertGreater(evaluate("document.getElementById('nested').scrollTop"), 0)
        preview.schedule_scrollbar_sync()
        QApplication.processEvents()
        evaluate("0")
        expected_vertical_max = int(evaluate(
            "document.scrollingElement.scrollHeight-document.scrollingElement.clientHeight"
        ))
        expected_horizontal_max = int(evaluate(
            "document.scrollingElement.scrollWidth-document.scrollingElement.clientWidth"
        ))
        self.assertEqual(preview.vertical_scrollbar.maximum(), expected_vertical_max)
        self.assertEqual(preview.horizontal_scrollbar.maximum(), expected_horizontal_max)
        preview.vertical_scrollbar.setValue(preview.vertical_scrollbar.maximum())
        preview.horizontal_scrollbar.setValue(preview.horizontal_scrollbar.maximum())
        QApplication.processEvents()
        self.assertAlmostEqual(
            evaluate("document.scrollingElement.scrollTop"),
            preview.vertical_scrollbar.maximum(),
            delta=1,
        )
        self.assertAlmostEqual(
            evaluate("document.scrollingElement.scrollLeft"),
            preview.horizontal_scrollbar.maximum(),
            delta=1,
        )
        QApplication.instance().removeEventFilter(preview)
        preview.close()


if __name__ == "__main__":
    unittest.main()
