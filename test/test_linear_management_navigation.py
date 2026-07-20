import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from main import MainWindow, ConversationSkillWizardDialog


class LinearManagementNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_sidebar_only_exposes_three_management_routes(self):
        self.assertEqual(
            set(self.window.product_nav_buttons),
            {"capabilities", "automation", "settings"},
        )
        self.assertFalse(hasattr(self.window, "sidebar_agent_module_btn"))
        self.assertFalse(hasattr(self.window, "sidebar_memory_btn"))
        self.assertFalse(hasattr(self.window, "sidebar_skill_capture_btn"))
        self.assertTrue(hasattr(self.window, "agent_picker_btn"))

    def test_settings_and_automation_use_main_page_stack_without_exec(self):
        with patch.object(QDialog, "exec", side_effect=AssertionError("large modal opened")):
            self.assertTrue(self.window.open_settings("个性与记忆"))
            self.assertEqual(self.window.current_product_route, "settings")
            self.assertIs(
                self.window.main_page_stack.currentWidget(),
                self.window.product_pages["settings"],
            )
            self.assertTrue(self.window.show_conversation_page())
            self.assertTrue(self.window.open_automation_center())
            self.assertEqual(self.window.current_product_route, "automation")
            self.window.show_conversation_page()
            self.window.skill_manager_ready = True
            self.assertTrue(self.window.open_skills_center())
            self.assertEqual(self.window.current_product_route, "capabilities")

    def test_automation_editor_is_embedded_and_dirty_aware(self):
        self.window.open_automation_center()
        self.assertTrue(self.window.show_automation_task_editor())
        editor = self.window.product_pages["automation_task_editor"]
        self.assertEqual(self.window.current_product_subroute, "task_editor")
        self.assertFalse(editor.save_btn.isEnabled())
        editor.name_input.setText("日报")
        self.app.processEvents()
        self.assertTrue(editor.is_dirty())
        self.assertTrue(editor.save_btn.isEnabled())

    def test_memory_is_a_settings_section_and_part_of_dirty_state(self):
        self.window.open_settings("个性与记忆")
        page = self.window.product_pages["settings"]
        self.assertEqual(page.nav_list.currentItem().text(), "个性与记忆")
        original = page.memory_soul_edit.toPlainText()
        page.memory_soul_edit.setPlainText(original + "\n保持直接。")
        self.app.processEvents()
        self.assertTrue(page._settings_dirty)
        self.assertTrue(page.save_settings_btn.isEnabled())

    def test_settings_navigation_compacts_below_threshold(self):
        self.window.open_settings("模型与服务")
        page = self.window.product_pages["settings"]
        self.window.resize(720, 720)
        self.window.show()
        self.app.processEvents()
        self.assertLess(page.width(), 760)
        self.assertFalse(page.nav_combo.isHidden())
        self.assertTrue(page.nav_list.isHidden())

    def test_skill_wizard_preserves_message_origin_selection(self):
        messages = [
            {"id": "u1", "role": "user", "content": "整理这个流程"},
            {"id": "a1", "role": "assistant", "content": "可复用步骤"},
            {"id": "u2", "role": "user", "content": "无关消息"},
        ]
        wizard = ConversationSkillWizardDialog([], messages, selected_message_ids=["u1", "a1"])
        try:
            self.assertEqual(
                [message["id"] for message in wizard.selected_messages()],
                ["u1", "a1"],
            )
            self.assertEqual(wizard.stack.currentIndex(), 0)
            wizard._go_next()
            self.assertEqual(wizard.stack.currentIndex(), 1)
            self.assertEqual(wizard.next_btn.text(), "开始复用分析")
        finally:
            wizard.deleteLater()


if __name__ == "__main__":
    unittest.main()
