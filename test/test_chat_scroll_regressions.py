import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from main import AutoResizingPlainTextEdit, AutoResizingTextEdit, ModelEditDialog


class WheelEventCapture(QObject):
    def __init__(self):
        super().__init__()
        self.pixel_deltas = []

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Wheel:
            self.pixel_deltas.append(event.pixelDelta())
        return super().eventFilter(watched, event)


class ChatScrollRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_long_plain_text_is_not_clipped_at_20000_pixels(self):
        editor = AutoResizingPlainTextEdit()
        editor.resize(640, 24)
        editor.setPlainText("long result\n" * 1800)
        editor.adjustHeight()
        self.assertGreater(editor.height(), 20000)
        self.assertFalse(editor.verticalScrollBar().isVisible())
        editor.deleteLater()

    def test_long_rich_text_is_not_clipped_at_20000_pixels(self):
        editor = AutoResizingTextEdit()
        editor.resize(640, 24)
        editor.setPlainText("long result\n" * 1800)
        editor.adjustHeight()
        self.assertGreater(editor.height(), 20000)
        self.assertFalse(editor.verticalScrollBar().isVisible())
        editor.deleteLater()

    def _assert_wheel_is_owned_by_chat_scroll(self, editor_class, pixel_delta=None, angle_delta=None):
        chat_scroll = QScrollArea()
        chat_scroll.setObjectName("ChatScrollArea")
        chat_scroll.resize(500, 240)
        container = QWidget()
        layout = QVBoxLayout(container)
        editor = editor_class()
        editor.resize(440, 24)
        editor.setPlainText(("line of text that wraps across the message body " * 5 + "\n") * 40)
        editor.adjustHeight()
        layout.addWidget(editor)
        chat_scroll.setWidget(container)
        chat_scroll.setWidgetResizable(True)
        chat_scroll.show()
        self.app.processEvents()
        editor.adjustHeight()
        self.app.processEvents()

        outer_bar = chat_scroll.verticalScrollBar()
        inner_bar = editor.verticalScrollBar()
        self.assertGreater(outer_bar.maximum(), 0)
        outer_bar.setValue(min(100, outer_bar.maximum()))
        inner_bar.setValue(inner_bar.maximum() // 2)
        outer_before = outer_bar.value()
        inner_before = inner_bar.value()
        capture = WheelEventCapture()
        chat_scroll.viewport().installEventFilter(capture)

        position = QPointF(editor.viewport().rect().center())
        global_position = QPointF(editor.viewport().mapToGlobal(position.toPoint()))
        wheel = QWheelEvent(
            position,
            global_position,
            pixel_delta or QPoint(),
            angle_delta or QPoint(),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(editor.viewport(), wheel)
        self.app.processEvents()

        if angle_delta:
            self.assertGreater(outer_bar.value(), outer_before)
        if pixel_delta:
            self.assertEqual(capture.pixel_deltas, [pixel_delta])
        self.assertEqual(inner_bar.value(), inner_before)
        chat_scroll.close()
        chat_scroll.deleteLater()

    def test_plain_text_wheel_scrolls_chat_instead_of_internal_document(self):
        self._assert_wheel_is_owned_by_chat_scroll(
            AutoResizingPlainTextEdit,
            angle_delta=QPoint(0, -120),
        )

    def test_rich_text_wheel_scrolls_chat_instead_of_internal_document(self):
        self._assert_wheel_is_owned_by_chat_scroll(
            AutoResizingTextEdit,
            angle_delta=QPoint(0, -120),
        )

    def test_pixel_wheel_delta_is_forwarded_to_chat_scroll(self):
        self._assert_wheel_is_owned_by_chat_scroll(
            AutoResizingPlainTextEdit,
            pixel_delta=QPoint(0, -24),
        )

    def test_new_gpt_5_6_model_defaults_to_responses(self):
        dialog = ModelEditDialog("openai")
        dialog.model_name_input.setText("gpt-5.6-terra")
        self.assertEqual(dialog.api_protocol_combo.currentData(), "responses")
        dialog.model_name_input.setText("gpt-4.1-mini")
        self.assertEqual(dialog.api_protocol_combo.currentData(), "chat_completions")
        dialog.deleteLater()

    def test_reasoning_effort_controls_use_protocol_names(self):
        dialog = ModelEditDialog(
            "openai",
            {
                "display_name": "Test model",
                "model_name": "test-model",
                "reasoning_efforts": ["low", "high", "max"],
                "reasoning_effort": "high",
            },
        )
        try:
            self.assertEqual(
                [dialog.reasoning_checks[key].text() for key in dialog.reasoning_checks],
                ["none", "low", "medium", "high", "xhigh", "max"],
            )
            self.assertEqual(
                [dialog.reasoning_combo.itemText(index) for index in range(dialog.reasoning_combo.count())],
                ["none", "low", "medium", "high", "xhigh", "max"],
            )
            self.assertEqual(dialog.reasoning_combo.currentText(), "high")
        finally:
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
