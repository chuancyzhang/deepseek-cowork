import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from core.theme import DesignTokens, apply_theme, apply_tooltip_theme, get_tech_stylesheet


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_tooltip_style_avoids_windows_translucent_rendering(self):
        stylesheet = get_tech_stylesheet("light")
        tooltip_rule = stylesheet.split("QToolTip {", 1)[1].split("}", 1)[0]

        self.assertIn(f"background-color: {DesignTokens.bg_main}", tooltip_rule)
        self.assertIn(f"color: {DesignTokens.text_primary}", tooltip_rule)
        self.assertNotIn("opacity", tooltip_rule)
        self.assertNotIn("border-radius", tooltip_rule)

    def test_apply_theme_aligns_native_tooltip_palette(self):
        apply_theme(self.app, "light")

        palette = self.app.palette()
        self.assertEqual(palette.color(QPalette.ToolTipBase).name(), DesignTokens.bg_main)
        self.assertEqual(palette.color(QPalette.ToolTipText).name(), DesignTokens.text_primary)

    def test_tooltip_only_theme_does_not_load_global_widget_styles(self):
        apply_tooltip_theme(self.app)

        stylesheet = self.app.styleSheet()
        self.assertIn("QToolTip {", stylesheet)
        self.assertNotIn("QPushButton", stylesheet)
        self.assertNotIn("QLineEdit", stylesheet)
        palette = self.app.palette()
        self.assertEqual(palette.color(QPalette.ToolTipBase).name(), DesignTokens.bg_main)
        self.assertEqual(palette.color(QPalette.ToolTipText).name(), DesignTokens.text_primary)


if __name__ == "__main__":
    unittest.main()
