import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QRawFont
from PySide6.QtWidgets import QApplication

from core.theme import DesignTokens
from main import (
    apple_button_style,
    apple_section_surface_style,
    initialize_desktop_theme,
    linear_dialog_stylesheet,
)


def _relative_luminance(hex_color):
    channels = []
    value = hex_color.lstrip("#")
    for offset in (0, 2, 4):
        channel = int(value[offset:offset + 2], 16) / 255.0
        channels.append(channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(foreground, background):
    lighter = max(_relative_luminance(foreground), _relative_luminance(background))
    darker = min(_relative_luminance(foreground), _relative_luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


class UiDesignSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_primary_text_pairs_meet_normal_text_contrast(self):
        self.assertGreaterEqual(_contrast_ratio(DesignTokens.text_primary, DesignTokens.bg_main), 4.5)
        self.assertGreaterEqual(_contrast_ratio(DesignTokens.text_secondary, DesignTokens.bg_main), 4.5)
        self.assertGreaterEqual(_contrast_ratio(DesignTokens.text_inverse, DesignTokens.primary), 4.5)

    def test_linear_semantic_tokens_cover_interaction_states(self):
        for token_name in (
            "primary_pressed",
            "primary_focus",
            "text_disabled",
            "bg_pressed",
            "bg_disabled",
        ):
            self.assertTrue(getattr(DesignTokens, token_name))

    def test_surface_styles_are_scoped_to_opted_in_frames(self):
        stylesheet = apple_section_surface_style()
        self.assertIn('QFrame[uiSurface="true"]', stylesheet)
        self.assertNotIn("QFrame {", stylesheet)

    def test_button_and_dialog_styles_cover_interaction_states(self):
        button_style = apple_button_style("primary")
        dialog_style = linear_dialog_stylesheet("ExampleDialog")
        self.assertIn("QPushButton:pressed", button_style)
        self.assertIn("QPushButton:disabled", button_style)
        self.assertIn("QDialog#ExampleDialog QLineEdit:focus", dialog_style)
        self.assertIn("QDialog#ExampleDialog QLineEdit:disabled", dialog_style)

    def test_desktop_font_can_render_chinese_ui_copy(self):
        initialize_desktop_theme(self.app)
        raw_font = QRawFont.fromFont(self.app.font())
        self.assertTrue(raw_font.isValid())
        self.assertTrue(raw_font.supportsCharacter(ord("设")))


if __name__ == "__main__":
    unittest.main()
