import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from main import AutoResizingPlainTextEdit, AutoResizingTextEdit, ModelEditDialog


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

    def test_new_gpt_5_6_model_defaults_to_responses(self):
        dialog = ModelEditDialog("openai")
        dialog.model_name_input.setText("gpt-5.6-terra")
        self.assertEqual(dialog.api_protocol_combo.currentData(), "responses")
        dialog.model_name_input.setText("gpt-4.1-mini")
        self.assertEqual(dialog.api_protocol_combo.currentData(), "chat_completions")
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
