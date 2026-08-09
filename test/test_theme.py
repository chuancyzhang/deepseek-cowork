import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from core.theme import (
    DesignTokens,
    ThemeRuntimeManager,
    apply_theme,
    apply_tooltip_theme,
    get_tech_stylesheet,
)
from core.theme_service import ThemeRepository


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

    def test_selection_and_sidebar_geometry_use_updated_product_tokens(self):
        self.assertNotEqual(DesignTokens.selection_bg, DesignTokens.primary_soft)
        self.assertEqual(DesignTokens.sidebar_width, 240)
        self.assertEqual(DesignTokens.sidebar_max_width, 320)

    def test_late_animation_failure_uses_existing_theme_error_channel_and_metadata_log(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ThemeRuntimeManager(self.app, ThemeRepository(directory))
            manager.current = {
                "id": "animated-theme",
                "preview": True,
                "assets": {
                    "background": {
                        "media_type": "image/gif",
                        "animation": {"frame_count": 2, "duration_ms": 200},
                    }
                },
            }
            errors = []
            manager.themeApplyFailed.connect(errors.append)

            manager.report_runtime_error("主题动态背景播放失败：background；decode error")

            self.assertEqual(errors, ["主题动态背景播放失败：background；decode error"])
            self.assertEqual(manager.last_failure["reason"], "runtime_theme")
            self.assertTrue(manager.last_failure["preview"])
            with open(os.path.join(directory, "theme_debug.log"), encoding="utf-8") as stream:
                log = stream.read()
            self.assertIn("animation_error", log)
            self.assertIn('animation_frame_count=2', log)
            self.assertIn('animation_duration_ms=200', log)


if __name__ == "__main__":
    unittest.main()
