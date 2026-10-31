"""First-use WebEngine must not recreate the visible Windows application shell."""
import hashlib
from pathlib import Path
import time
import unittest

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication

import test_workspace_presentation as presentation


class SurfaceEvents(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.events = []

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() in {
            QEvent.Hide, QEvent.Show, QEvent.WinIdChange, QEvent.PlatformSurface,
        }:
            self.events.append(event.type().name)
        return False


class WindowRenderSurfaceTests(unittest.TestCase):
    setUpClass = classmethod(presentation.WorkspacePresentationTests.setUpClass.__func__)
    tearDown = presentation.WorkspacePresentationTests.tearDown

    def setUp(self):
        if QApplication.platformName() != "windows":
            self.skipTest("Requires QT_QPA_PLATFORM=windows; offscreen cannot verify HWND continuity")
        presentation.WorkspacePresentationTests.setUp(self)
        self.tracker = SurfaceEvents(self.window)
        self.window.installEventFilter(self.tracker)
        self.initial_handle = int(self.window.winId())
        self.initial_input = self.window.input_field
        self.window.input_field.setPlainText("首次打开后保留的输入")
        self.assertIsNone(self.window.deliverable_web_view)

    def until(self, predicate):
        deadline = time.monotonic() + 8
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate())

    def check_visible_document(self, view, expression="document.body.innerText"):
        result = []
        view.page().runJavaScript(expression, result.append)
        self.until(lambda: bool(result))
        self.assertIn("首次打开内容", result[0])
        self.assertTrue(view.isVisible())
        self.assertEqual(int(self.window.winId()), self.initial_handle)
        self.assertEqual(self.tracker.events, [])
        self.assertIs(self.window.input_field, self.initial_input)
        self.assertEqual(self.window.input_field.toPlainText(), "首次打开后保留的输入")

    def test_first_workspace_preview_keeps_native_window(self):
        path = Path(self.temp.name, "first-preview.md")
        path.write_text("# 首次打开内容", encoding="utf-8")
        self.window._apply_workspace_to_ui(self.temp.name, refresh_sidebar=False,
            remember_workspace=False, persist_default=False)
        self.window.show_context_drawer(self.window.RIGHT_TAB_FILES)
        self.window.select_deliverable(str(path), render_html=True)
        self.window.set_file_navigator_visible(False, reason="render_surface_test")
        self.until(lambda: self.window.deliverable_web_view is not None)
        view = self.window.deliverable_web_view
        self.until(lambda: self.window.get_current_session().deliverable_preview_rendered)
        self.check_visible_document(view)

    def test_first_conversation_visualization_keeps_native_window(self):
        from main import InlineVisualizationCard
        state = self.window.get_current_session()
        self.window.add_chat_bubble("User", "带交互视图的历史对话", animate=False)
        path = Path(self.temp.name, "first-visualization.html")
        path.write_text("<div>首次打开内容</div>", encoding="utf-8")
        card = InlineVisualizationCard({"path": str(path), "file": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
            state.session_id, None, read_only=True, parent=state.chat_scroll.widget())
        state.chat_layout.insertWidget(state.chat_layout.count() - 1, card)
        card.ensure_loaded()
        self.assertIsNotNone(card.web_view, card._visualization_error)
        self.until(lambda: card.status_label.isHidden())
        # The fragment uses an opaque-origin sandbox; inspect its loaded srcdoc
        # without weakening that sandbox to reach the child DOM.
        self.check_visible_document(card.web_view, "document.getElementById('frame').srcdoc")


if __name__ == "__main__":
    unittest.main()
