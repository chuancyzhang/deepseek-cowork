import os
import unittest
import main as main_module

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QRawFont
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from core.theme import DesignTokens
from main import (
    AgentModuleDialog,
    MemoryUpdateDialog,
    SkillsCenterDialog,
    apple_button_style,
    apple_section_surface_style,
    initialize_desktop_theme,
    linear_dialog_stylesheet,
)
from ui.primitives import (
    ProductEmptyState,
    ProductInputDialog,
    ProductMessageBox,
    ProductMessageDialog,
    ProductPageHeader,
    ProductInlineNotice,
    ProductMasterDetail,
    ProductNavigationRow,
    ProductSegmentedControl,
    ProductStatusBadge,
    ProductToolbar,
    product_button_style,
    product_code_style,
    product_segmented_style,
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

    def test_product_geometry_uses_shared_density(self):
        self.assertEqual(DesignTokens.control_height, 32)
        self.assertEqual(DesignTokens.row_height, 36)
        self.assertLessEqual(DesignTokens.radius_md, 8)
        self.assertEqual(DesignTokens.spacing_xs, 4)

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

    def test_shared_primitives_cover_product_states(self):
        self.assertIn("QPushButton:pressed", product_button_style("primary"))
        self.assertIn("QPushButton:focus", product_button_style("secondary"))
        self.assertIn("QPushButton:checked", product_segmented_style())
        self.assertIn("Cascadia Mono", product_code_style())
        header = ProductPageHeader("能力中心", "搜索和配置能力")
        empty = ProductEmptyState("暂无内容", "创建后会显示在这里", "创建")
        badge = ProductStatusBadge("运行中", "primary")
        toolbar = ProductToolbar()
        toolbar.add_search("搜索能力")
        toolbar.finish()
        segmented = ProductSegmentedControl((("all", "全部"), ("enabled", "已启用")))
        row = ProductNavigationRow("能力", "已启用")
        notice = ProductInlineNotice("运行中", "info")
        master_detail = ProductMasterDetail(QWidget(), QWidget())
        self.assertEqual(header.findChildren(QLabel)[0].text(), "能力中心")
        self.assertIsNotNone(empty.action_button)
        self.assertEqual(badge.text(), "运行中")
        self.assertIsNotNone(toolbar.search_input)
        self.assertTrue(segmented.buttons["all"].isChecked())
        self.assertTrue(row.isCheckable())
        self.assertEqual(notice.label.text(), "运行中")
        self.assertFalse(master_detail.detail_visible)

    def test_business_message_and_input_calls_use_product_facades(self):
        self.assertIs(main_module.QMessageBox, ProductMessageBox)
        self.assertIs(main_module.QInputDialog, ProductInputDialog)
        dialog = ProductMessageDialog("删除文件", "此操作无法撤销。", "destructive")
        try:
            self.assertEqual(dialog.objectName(), "ProductMessageDialog")
            self.assertLessEqual(DesignTokens.radius_md, 8)
        finally:
            dialog.deleteLater()

    def test_capability_master_detail_methods_belong_to_capability_center(self):
        self.assertTrue(hasattr(SkillsCenterDialog, "_build_skill_master_detail"))
        self.assertTrue(hasattr(SkillsCenterDialog, "_build_skill_detail"))
        self.assertFalse(hasattr(AgentModuleDialog, "_build_skill_master_detail"))

    def test_memory_update_dialog_uses_scoped_code_panels(self):
        dialog = MemoryUpdateDialog("全部历史")
        try:
            self.assertIn("QTextEdit#memoryProcessLog", dialog.process_log.styleSheet())
            self.assertIn("QTextEdit#memoryDraftEditor", dialog.editor.styleSheet())
            self.assertEqual(dialog.save_btn.property("variant"), "primary")
        finally:
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
