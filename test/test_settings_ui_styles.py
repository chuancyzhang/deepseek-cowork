import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QComboBox, QLabel

from core.theme import DesignTokens
from main import apply_settings_combo_style, build_settings_page_header


class SettingsComboStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selected_item_remains_legible_in_all_focus_states(self):
        combo = QComboBox()
        combo.addItems(["PyPI", "阿里云镜像"])

        apply_settings_combo_style(combo)

        view = combo.view()
        palette = view.palette()
        stylesheet = view.styleSheet()
        self.assertEqual(palette.color(QPalette.Highlight).name(), DesignTokens.primary_soft)
        self.assertEqual(palette.color(QPalette.HighlightedText).name(), DesignTokens.text_primary)
        self.assertIn("QAbstractItemView::item:selected:!active", stylesheet)
        self.assertIn("QAbstractItemView::item:selected:!focus", stylesheet)
        self.assertIn(f"color: {DesignTokens.text_primary}", stylesheet)

    def test_settings_page_header_renders_intro_copy(self):
        header = build_settings_page_header("模型与服务", "配置常用模型入口。")

        intro = header.findChild(QLabel, "SettingsPageIntro")
        self.assertIsNotNone(intro)
        self.assertEqual(intro.text(), "配置常用模型入口。")
        self.assertTrue(intro.wordWrap())


if __name__ == "__main__":
    unittest.main()
