import os
import json
import unittest
import main as main_module
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QRawFont
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from core.theme import DesignTokens
from main import (
    AgentModuleDialog,
    AgentProfileManager,
    AdvancedSkillsCenterDialog,
    CAPABILITY_SCENES,
    MemoryUpdateDialog,
    SkillsCenterDialog,
    StatusPill,
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

    def test_status_pill_reserves_width_for_network_retry_copy(self):
        retry_text = "网络连接中断，正在重试 3/5"
        pill = StatusPill("处理中")
        pill.setText(retry_text)
        pill.resize(pill.minimumWidth(), pill.sizeHint().height())
        pill.show()
        self.app.processEvents()

        self.assertEqual(pill.text(), retry_text)
        self.assertEqual(pill.text_label.text(), retry_text)
        self.assertGreater(pill.minimumWidth(), 100)
        self.assertLessEqual(pill.minimumWidth(), StatusPill.MAX_WIDTH)

    def test_plain_empty_state_supports_icon_secondary_action_and_content_updates(self):
        empty = ProductEmptyState(
            "暂无文件",
            "创建后会显示在这里",
            "打开目录",
            appearance="plain",
            icon=main_module.sidebar_symbol_icon("folder-open", DesignTokens.text_secondary, 18),
            action_kind="secondary",
        )
        empty.set_content("还没有文件", "在对话中创建文件。")
        empty.set_action("在资源管理器中打开")

        self.assertEqual(empty.appearance, "plain")
        self.assertIsNotNone(empty.icon_label)
        self.assertEqual(empty.title_label.text(), "还没有文件")
        self.assertEqual(empty.description_label.text(), "在对话中创建文件。")
        self.assertEqual(empty.action_button.text(), "在资源管理器中打开")
        self.assertIn("SecondaryBtn", empty.action_button.objectName())

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
        self.assertFalse(hasattr(SkillsCenterDialog, "_build_skill_master_detail"))
        self.assertTrue(hasattr(AdvancedSkillsCenterDialog, "_build_skill_master_detail"))
        self.assertTrue(hasattr(AdvancedSkillsCenterDialog, "_build_skill_detail"))
        self.assertFalse(hasattr(AgentModuleDialog, "_build_skill_master_detail"))

    def test_capability_library_uses_five_scenes_and_keeps_user_skills_separate(self):
        manager = MagicMock()
        manager.get_all_skills.return_value = [
            {
                "name": "web-search",
                "display_name": "网页搜索",
                "source_type": "bundled_plugin",
                "enabled": False,
                "presentation": {
                    "category": "search_browse",
                    "short_name": "网页搜索",
                    "summary": "搜索实时网页信息。",
                    "examples": ["查询最新信息"],
                    "access_note": "搜索词会发送到搜索服务。",
                },
            },
            {
                "name": "my-skill",
                "display_name": "我的整理助手",
                "description_cn": "整理我的项目资料。",
                "enabled": True,
            },
        ]
        manager.is_skill_editable.side_effect = lambda name: name == "my-skill"
        config = MagicMock()
        page = SkillsCenterDialog(manager, config)
        try:
            self.assertEqual(
                list(CAPABILITY_SCENES.values()),
                ["查找资料", "处理文档", "分析数据", "制作内容", "金融研究"],
            )
            self.assertIn("my-skill", page._user_owned_names)
            self.assertEqual([item["name"] for item in page._official_skills()], ["web-search"])
            page.show()
            page.resize(879, 680)
            self.app.processEvents()
            self.assertEqual(page._column_count, 1)
            page.resize(900, 680)
            self.app.processEvents()
            self.assertEqual(page._column_count, 2)
            page._set_mode("mine")
            labels = {label.text() for label in page.findChildren(QLabel)}
            self.assertIn("我的能力", labels)
            self.assertIn("整理我的项目资料。", labels)
            visible_copy = " ".join(
                [label.text() for label in page.findChildren(QLabel)]
                + [button.text() for button in page.findChildren(QPushButton)]
            )
            for technical_term in ("MCP", "Tool", "Script", "Medium Risk"):
                self.assertNotIn(technical_term, visible_copy)
        finally:
            page.deleteLater()

    def test_capability_store_card_exposes_enabled_state_and_direct_close(self):
        manager = MagicMock()
        manager.get_all_skills.return_value = [
            {
                "name": "capability-one",
                "display_name": "能力一",
                "source_type": "bundled_plugin",
                "enabled": True,
                "presentation": {
                    "category": "search_browse",
                    "short_name": "能力一",
                    "summary": "用于测试状态区域。",
                    "examples": ["任务一", "任务二"],
                    "access_note": "测试说明。",
                },
            }
        ]
        manager.is_skill_editable.return_value = False
        config = MagicMock()

        def update_enabled(name, enabled):
            manager.get_all_skills.return_value[0]["enabled"] = bool(enabled)

        config.set_skill_enabled.side_effect = update_enabled
        page = SkillsCenterDialog(manager, config)
        try:
            self.assertFalse(
                [
                    switch
                    for switch in page.findChildren(main_module.AppleSwitch)
                    if switch.objectName() == "CapabilityEnableSwitch"
                ]
            )
            state_labels = [
                label.text()
                for label in page.findChildren(QLabel)
                if label.objectName() == "CapabilityStateLabel"
            ]
            self.assertIn("✓ 已开启", state_labels)
            close_buttons = [
                button
                for button in page.findChildren(QPushButton)
                if button.objectName() == "CapabilityDisableAction"
            ]
            self.assertEqual(len(close_buttons), 1)
            close_buttons[0].click()
            self.app.processEvents()
            config.set_skill_enabled.assert_called_once_with("capability-one", False)
            enable_buttons = [
                button
                for button in page.findChildren(QPushButton)
                if button.objectName() == "CapabilityEnableAction"
            ]
            self.assertEqual([button.text() for button in enable_buttons], ["开启"])
        finally:
            page.deleteLater()

    def test_browser_card_requires_both_skill_and_browser_connection(self):
        manager = MagicMock()
        manager.get_all_skills.return_value = [
            {
                "name": "browser-automation",
                "display_name": "浏览器自动化",
                "source_type": "bundled_plugin",
                "enabled": True,
                "presentation": {
                    "category": "search_browse",
                    "short_name": "浏览器操作",
                    "summary": "读取和操作网页。",
                    "examples": ["读取登录后的网页", "填写网页表单"],
                    "access_note": "可访问浏览器登录态。",
                },
            }
        ]
        manager.is_skill_editable.return_value = False
        config = MagicMock()
        page = SkillsCenterDialog(manager, config)
        try:
            state_labels = [
                label.text()
                for label in page.findChildren(QLabel)
                if label.objectName() == "CapabilityStateLabel"
            ]
            self.assertIn("还需完成一次简单设置", state_labels)
            self.assertNotIn("✓ 已开启", state_labels)
            self.assertEqual(
                [
                    button.text()
                    for button in page.findChildren(QPushButton)
                    if button.objectName() == "BrowserAutomationSetupAction"
                ],
                ["继续设置"],
            )
            self.assertEqual(
                len(
                    [
                        button
                        for button in page.findChildren(QPushButton)
                        if button.objectName() == "CapabilityDisableAction"
                    ]
                ),
                1,
            )

            page.browser_component_status = {
                "known": True,
                "installed": True,
                "ready": True,
            }
            page._render_content()
            self.app.processEvents()
            ready_labels = [
                label.text()
                for label in page.findChildren(QLabel)
                if label.objectName() == "CapabilityStateLabel"
            ]
            self.assertIn("✓ 已开启", ready_labels)
            self.assertEqual(
                [
                    button.text()
                    for button in page.findChildren(QPushButton)
                    if button.objectName() == "BrowserAutomationSettingsAction"
                ],
                ["设置"],
            )
        finally:
            page.deleteLater()

    def test_bundled_capabilities_have_complete_linear_presentation_metadata(self):
        root = Path(__file__).resolve().parents[1] / "ai_skills"
        bundled = []
        for manifest_path in root.glob("*/skill.json"):
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if payload.get("source_type") != "bundled_plugin":
                continue
            bundled.append(payload["name"])
            self.assertTrue(str(payload.get("display_name") or "").strip(), payload["name"])
            presentation = payload.get("presentation")
            self.assertIsInstance(presentation, dict, payload["name"])
            self.assertIn(presentation.get("category"), CAPABILITY_SCENES, payload["name"])
            self.assertTrue(str(presentation.get("short_name") or "").strip(), payload["name"])
            self.assertTrue(str(presentation.get("summary") or "").strip(), payload["name"])
            self.assertGreaterEqual(len(presentation.get("examples") or []), 2, payload["name"])
            self.assertTrue(str(presentation.get("access_note") or "").strip(), payload["name"])
        self.assertGreaterEqual(len(bundled), 14)

    def test_empty_agent_settings_create_a_disabled_template(self):
        manager = AgentProfileManager([], lambda: [])
        try:
            profiles = manager.get_profiles()
            self.assertEqual(len(profiles), 1)
            self.assertEqual(profiles[0]["name"], "新智能体")
            self.assertFalse(profiles[0]["enabled"])
            self.assertFalse(manager.enabled_check.isChecked())
            self.assertIn("已停用", manager.profile_list.item(0).text())
        finally:
            manager.deleteLater()

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
