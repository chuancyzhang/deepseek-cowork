import os
import inspect
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from main import (
    ChatBubble,
    ComposerActionPopover,
    ConversationSkillOptionsDialog,
    ConversationSkillRangeDialog,
    InlineInteractionCard,
    MainWindow,
    ModelSelectorPopover,
    SearchableSkillPickerButton,
    SessionSkillPickerPopover,
)


class ConversationLinearInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        self.app.processEvents()

    def test_skill_picker_searches_description_and_preserves_hidden_selection(self):
        skills = [
            {"name": "company-one-page", "display_name": "Company One Page", "description": "企业单页分析"},
            {"name": "market-brief", "display_name": "Market Brief", "description": "市场日报"},
        ]
        picker = SessionSkillPickerPopover(skills, ["company-one-page"])
        self.assertFalse(picker.apply_btn.isEnabled())
        self.assertFalse(picker.selection_chip_scroll.isHidden())
        self.assertEqual(picker.selection_chip_layout.count(), 2)
        picker._filter_items("市场日报")
        self.assertTrue(picker._items[0].isHidden())
        self.assertIn("company-one-page", picker.selected_names)
        picker.clear_selection()
        self.assertTrue(picker.apply_btn.isEnabled())
        self.assertTrue(picker.selection_chip_scroll.isHidden())
        picker.deleteLater()

    def test_model_picker_keeps_channel_model_and_effort(self):
        profiles = [
            {
                "id": "grok-45",
                "channel_display_name": "grok",
                "display_name": "grok-4.5",
                "model_name": "grok-4.5",
                "reasoning_efforts": ["low", "high"],
                "reasoning_effort": "high",
            }
        ]
        picker = ModelSelectorPopover(profiles, "grok-45")
        self.assertEqual(picker.model_list.item(0).text(), "grok / grok-4.5")
        effort_buttons = [picker.effort_row.itemAt(i).widget() for i in range(picker.effort_row.count())]
        self.assertTrue(any(button and button.text() == "高" and button.isChecked() for button in effort_buttons))
        picker.deleteLater()

    def test_composer_add_popover_has_no_duplicate_agent_entry(self):
        source = inspect.getsource(ComposerActionPopover.__init__)
        for title in ("添加文件", "指定能力", "沉淀为 Skill"):
            self.assertIn(title, source)
        self.assertNotIn("添加智能体", source)

    def test_inline_choice_request_resolves_without_dialog(self):
        card = InlineInteractionCard(
            {
                "kind": "choice",
                "title": "选择环境",
                "message": "请选择要查看的环境",
                "options": [{"label": "运行环境", "value": "runtime"}],
                "timeout_seconds": 120,
            }
        )
        values = []
        card.resolved.connect(values.append)
        card.option_checks[0].setChecked(True)
        card._submit()
        self.assertEqual(values, ["runtime"])
        self.assertFalse(card.submit_btn.isEnabled())
        card.deleteLater()

    def test_thinking_stays_expanded_when_finalized(self):
        bubble = ChatBubble("Agent", "")
        bubble.update_thinking("先检查环境")
        bubble.think_toggle_btn.setChecked(True)
        bubble.update_thinking(duration=2.5, is_final=True)
        self.assertTrue(bubble.think_toggle_btn.isChecked())
        self.assertIn("2.5 秒", bubble.think_toggle_btn.text())
        bubble.deleteLater()

    def test_short_user_message_uses_natural_width(self):
        bubble = ChatBubble("User", "你好")
        bubble.apply_dynamic_widths(880, 640)
        self.assertLess(bubble.user_content_edit.width(), 200)
        bubble.deleteLater()

    def test_skill_update_uses_searchable_target_not_native_combo(self):
        parent = QWidget()
        parent.skill_manager = SimpleNamespace(is_skill_editable=lambda _name: True)
        skills = [{"name": "editable-skill", "display_name": "Editable Skill", "description": "可编辑"}]
        dialog = ConversationSkillOptionsDialog(skills, parent)
        self.assertIsInstance(dialog.target_combo, SearchableSkillPickerButton)
        self.assertNotIsInstance(dialog.target_combo, QComboBox)
        dialog.deleteLater()
        parent.deleteLater()

    def test_skill_range_hides_injected_context_and_supports_quick_ranges(self):
        messages = [
            {
                "id": "context",
                "role": "system",
                "content": "ctx",
                "meta": {"kind": "skill_context", "hidden": True, "source": "skill_prompt"},
            },
            {"id": "u1", "role": "user", "content": "问题"},
            {"id": "a1", "role": "assistant", "content": "回答"},
        ]
        dialog = ConversationSkillRangeDialog(messages)
        self.assertEqual(dialog.message_list.count(), 2)
        dialog._apply_quick_range("current")
        self.assertEqual(len(dialog.selected_messages()), 2)
        dialog.deleteLater()

    def test_relative_time_uses_product_copy(self):
        now = __import__("time").time()
        self.assertEqual(MainWindow._format_project_session_age(None, now), "刚刚")
        self.assertIn("分钟前", MainWindow._format_project_session_age(None, now - 120))


if __name__ == "__main__":
    unittest.main()
