"""Interaction regressions for the shared home/conversation presentation."""
import os
import copy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from shiboken6 import isValid

from core.config_manager import ConfigManager
from core.theme import ThemeRuntimeManager
from core.theme_presets import builtin_theme
from core.theme_service import ThemeRepository
from main import MainWindow
from ui.primitives import SidebarInlineNameEditor


class WorkspacePresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for filename in ("msyh.ttc", "consola.ttf"):
            font = Path("C:/Windows/Fonts", filename)
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cowork-presentation-test-")
        self.addCleanup(self.temp.cleanup)
        for name in ("core.config_manager.get_app_data_dir", "core.config_manager.get_base_dir", "main.get_app_data_dir"):
            self.enterContext(patch(name, return_value=self.temp.name))
        self.enterContext(patch.object(MainWindow, "start_background_services", lambda self: None))
        self.previous_manager = getattr(self.app, "theme_manager", None)
        self.previous_registry = getattr(self.app, "theme_binding_registry", None)
        self.repository = ThemeRepository(self.temp.name)
        self.manager = ThemeRuntimeManager(self.app, self.repository)
        self.app.theme_manager = self.manager
        self.window = MainWindow(config_manager=ConfigManager(), theme_manager=self.manager)
        self.window._startup_hydration_scheduled = True
        self.window._background_services_scheduled = True
        self.window.resize(1200, 800)
        self.window.show()
        QTest.qWait(80)

    def tearDown(self):
        self.manager.restore_saved_theme(reason="test_cleanup")
        self.manager.stop()
        self.window.close()
        deadline = time.monotonic() + 5
        while not getattr(self.window, "_checkpoint_close_ready", False) and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(self.window._checkpoint_close_ready)
        self.window.file_model.directoryLoaded.disconnect()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.theme_manager = self.previous_manager
        self.app.theme_binding_registry = self.previous_registry

    def test_shared_composer_keeps_drafts_across_home_chat_switch_and_preview(self):
        window = self.window
        first = window.get_current_session()
        original_input = window.input_field
        self.assertTrue(window._home_presentation_active)
        self.assertFalse(window.workspace_title_label.isVisible())
        window.input_field.setPlainText("第一段未发送输入")
        window.create_new_session()
        second = window.get_current_session()
        window.input_field.setPlainText("第二段未发送输入")
        window.activate_session(first.session_id)
        self.assertEqual(window.input_field.toPlainText(), "第一段未发送输入")
        window.add_chat_bubble("User", "已发送的示例问题", animate=False)
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(isValid(original_input))
        self.assertIs(window.input_field, original_input)
        self.assertIs(window.input_row.parentWidget(), window.conversation_column)
        self.assertFalse(window._home_presentation_active)
        self.assertTrue(window.workspace_title_label.isVisible())
        saved_path = Path(self.repository.store_path)
        saved_before = saved_path.read_bytes() if saved_path.exists() else None
        self.assertTrue(self.manager.apply_profile(builtin_theme("default", dark=True), preview=True), self.manager.last_error)
        window.activate_session(second.session_id)
        self.assertTrue(window._home_presentation_active)
        self.assertEqual(window.input_field.toPlainText(), "第二段未发送输入")
        self.assertIs(window.input_row.parentWidget(), window.home_page)
        self.assertEqual(saved_path.read_bytes() if saved_path.exists() else None, saved_before)

    def test_header_rename_reuses_persistence_and_recovers_from_cancel_and_failure(self):
        window = self.window
        state = window.get_current_session()
        window.add_chat_bubble("User", "已开始的对话", animate=False)

        def edit(value, key):
            window._edit_current_conversation_title()
            editor = next(item for item in window.top_bar_widget.findChildren(SidebarInlineNameEditor) if not item.isHidden())
            editor.setText(value)
            QTest.keyClick(editor, key)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

        edit("产品讨论", Qt.Key_Return)
        record = window.chat_storage.get_conversation_record(state.session_id)
        self.assertEqual(record["title"], "产品讨论")
        self.assertTrue(record["meta"]["manual_title"])
        edit("放弃的改名", Qt.Key_Escape)
        self.assertEqual(window.workspace_title_label.text(), "产品讨论")
        with patch.object(window.chat_storage, "upsert_conversation", side_effect=OSError("保存失败示例")):
            edit("不能保存的改名", Qt.Key_Return)
        self.assertEqual(window.chat_storage.get_conversation_record(state.session_id)["title"], "产品讨论")
        self.assertTrue(window.workspace_title_label.isVisible())

    def test_permission_popover_retains_checkbox_and_save_failure_state(self):
        window = self.window
        checkbox = window.composer_permission
        original = checkbox.isChecked()
        window._show_composer_permission()
        self.assertTrue(window.permission_popover.isVisible())
        QTest.mouseClick(window.permission_choices[not original][0], Qt.LeftButton)
        self.assertEqual(window.config_manager.get_god_mode(), not original)
        window.permission_popover.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertTrue(isValid(checkbox))
        window._show_composer_permission()
        with patch.object(window.config_manager, "set_god_mode", side_effect=OSError("只读配置")):
            QTest.mouseClick(window.permission_choices[original][0], Qt.LeftButton)
        self.assertEqual(checkbox.isChecked(), not original)
        self.assertIn("完全访问" if not original else "请求批准", window.permission_button.text())

    def test_drawer_and_scrolling_leave_header_and_composer_reachable(self):
        window = self.window
        window.resize(1100, 760)
        state = window.get_current_session()
        window.add_chat_bubble("Agent", "\n\n".join(["这是用于检查历史消息滚动的完整段落。" * 5] * 18), animate=False)
        state.selected_skill_names = ["需要保留完整名称的项目资料整理能力" * 8]
        window.refresh_selected_skill_controls()
        window.show_context_drawer(window.RIGHT_TAB_OBSERVABILITY)
        QTest.qWait(180)
        window.sync_context_drawer_layout()
        QTest.qWait(60)
        self.assertTrue(window.composer_context_tags.isVisible())
        self.assertEqual(window.right_sidebar.y(), window.top_bar_widget.y())
        for control in (window.workspace_title_label, state.header_tools):
            self.assertLess(control.mapTo(window.main_container, control.rect().bottomRight()).x(), window.right_sidebar.x())
        composer_position = window.input_card.mapTo(window, QPoint(0, 0))
        header_position = window.top_bar_widget.pos()
        bar = state.chat_scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.maximum() // 2)
        QTest.qWait(30)
        self.assertEqual(window.input_card.mapTo(window, QPoint(0, 0)), composer_position)
        self.assertEqual(window.top_bar_widget.pos(), header_position)
        for button in (window.tool_menu_btn, window.composer_context_tags, window.permission_button, window.model_select_btn, window.action_btn):
            bottom_right = button.mapTo(window.input_card, button.rect().bottomRight())
            self.assertTrue(window.input_card.rect().contains(bottom_right), button.objectName() or button.text())

    def test_question_directory_keeps_long_titles_inside_and_targets_selected_question(self):
        from ui.primitives import ProductPopover

        window = self.window
        state = window.get_current_session()
        state.messages = [{"id": f"question-{index}", "role": "user", "content": "需要完整保留的长问题标题" * 20} for index in range(30)]
        window._retire_session_empty_state(state, reason="test_directory_fixture")
        anchor = next(button for button in state.header_tools.findChildren(QPushButton) if button.accessibleName() == "历史提问")
        window._show_question_directory(anchor)
        QTest.qWait(50)
        popover = next(item for item in window.findChildren(ProductPopover) if item.isVisible())
        entries = popover.findChildren(QPushButton)
        self.assertEqual(len(entries), 30)
        self.assertLessEqual(popover.width(), 380)
        self.assertTrue(entries[-1].toolTip().startswith("需要完整保留"))
        with patch.object(window, "jump_to_question") as jump:
            entries[-1].click()
            jump.assert_called_once_with("question-29")

    def test_header_tools_keep_session_ownership_and_popovers_close_on_switch(self):
        window = self.window
        first = window.get_current_session()
        window.add_chat_bubble("User", "第一段对话", animate=False)
        window.input_field.setPlainText("第一段草稿")
        first.token_usage_label.click()
        self.assertTrue(first.token_usage_label._popover.isVisible())
        second_id = window.create_new_session(make_current=True)
        second = window.get_session(second_id)
        window.add_chat_bubble("User", "第二段对话", animate=False)
        QTest.qWait(30)
        self.assertIs(window.session_tools_stack.currentWidget(), second.header_tools)
        self.assertFalse(first.token_usage_label._popover.isVisible())
        self.assertFalse(first.header_tools.isVisible())
        self.assertTrue(second.token_usage_label.isVisible())
        # Background updates must not replace the current session's header.
        first.token_usage_label.setText("后台用量 300K")
        self.assertNotIn("300K", second.token_usage_label.accessibleDescription())
        window.activate_session(first.session_id)
        self.assertIs(window.session_tools_stack.currentWidget(), first.header_tools)
        self.assertEqual(window.input_field.toPlainText(), "第一段草稿")
        search = next(b for b in first.header_tools.findChildren(QPushButton) if b.accessibleName() == "对话内搜索")
        search.click()
        self.assertTrue(window.conversation_search.isVisible())
        window.show_product_page(window.PAGE_SETTINGS)
        self.assertFalse(window.session_tools_stack.isVisible())
        window.show_product_page(window.PAGE_CONVERSATION)
        self.assertIs(window.session_tools_stack.currentWidget(), first.header_tools)
        tools = first.header_tools
        window.close_session_tab(window.session_tabs.indexOf(first.session_widget))
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertFalse(isValid(tools))
        self.assertNotIn(first.session_id, window.sessions)
        self.assertIs(window.session_tools_stack.currentWidget(), second.header_tools)

    def test_context_overview_keeps_full_request_available(self):
        window = self.window
        state = window.get_current_session()
        state.system_prompt_text = "完整前缀\n" + "x" * 7500 + "\n完整结尾"
        state.runtime_context_text = "本次运行上下文"
        state.knowledge_refs = [{"title": "产品说明"}]
        window.refresh_observability_view(state.session_id)
        self.assertEqual(window.context_summary_fields["references"].text(), "产品说明")
        self.assertTrue(window.observability_prompt_edit.isHidden())
        window.context_raw_toggle.setChecked(True)
        self.assertFalse(window.observability_prompt_edit.isHidden())
        self.assertIn("完整结尾", window.observability_prompt_edit.toPlainText())
        window.copy_observability_full_prompt()
        self.assertIn("完整结尾", QApplication.clipboard().text())
        self.assertIn("本次运行上下文", QApplication.clipboard().text())

    def test_knowledge_chips_restore_selection_and_stay_in_their_session(self):
        window = self.window
        first = window.get_current_session()
        refs = [{"title": "交割答疑手册", "source": "weknora", "knowledge_id": "doc-a"},
                {"title": "交割答疑手册", "source": "tencent-docs", "item_id": "doc-b"}]
        first.knowledge_refs = copy.deepcopy(refs)
        window.input_field.setPlainText("保留未发送的问题")
        window.refresh_context_badges()
        chips = window.composer_context_tags.chips("knowledge")
        self.assertEqual(len(chips), 2)
        self.assertTrue(all(chip.isVisible() for chip in chips))
        self.assertIn("WeKnora", chips[0].label.toolTip())
        self.assertIn("腾讯文档", chips[1].label.toolTip())
        window.refresh_context_badges()
        self.assertIs(window.composer_context_tags.chips("knowledge")[0], chips[0])
        with patch.object(window, "add_system_toast") as toast:
            chips[0].close_btn.click()
        self.assertEqual(first.knowledge_refs, refs[1:])
        undo = toast.call_args.kwargs["actions"][0][1]
        window.create_new_session()
        second = window.get_current_session()
        self.assertFalse(window.composer_context_tags.chips("knowledge"))
        undo()
        self.assertEqual(first.knowledge_refs, refs)
        self.assertEqual(second.knowledge_refs, [])
        self.assertFalse(window.composer_context_tags.chips("knowledge"))
        window.activate_session(first.session_id)
        self.assertEqual(len(window.composer_context_tags.chips("knowledge")), 2)
        self.assertEqual(window.input_field.toPlainText(), "保留未发送的问题")
        self.assertTrue(window.save_chat_history(first.session_id, flush=True))
        self.assertEqual(window.chat_storage.get_conversation_record(first.session_id)["meta"]["knowledge_refs"], refs)

    def test_knowledge_management_cancel_apply_and_stale_undo_preserve_new_choices(self):
        window = self.window
        state = window.get_current_session()
        first = {"title": "手册", "source": "weknora", "knowledge_id": "doc-a"}
        second = {"title": "新增资料", "source": "weknora", "knowledge_id": "doc-b"}
        state.knowledge_refs = [first]
        state.messages = [{"id": "saved-user", "role": "user", "content": "已有提问"}]
        before_messages = copy.deepcopy(state.messages)
        window.refresh_context_badges()
        window.composer_context_tags.chips("knowledge")[0].main_btn.click()
        popover = window.knowledge_reference_popover
        self.assertTrue(popover.isVisible())
        popover.items.item(0).setCheckState(Qt.Unchecked)
        popover.cancel_button.click()
        self.assertEqual(state.knowledge_refs, [first])
        window.composer_context_tags.chips("knowledge")[0].main_btn.click()
        popover = window.knowledge_reference_popover
        popover.items.item(0).setCheckState(Qt.Unchecked)
        popover.apply_button.click()
        self.assertFalse(window.composer_context_tags.chips("knowledge"))
        state.knowledge_refs = [first]
        window.refresh_context_badges()
        with patch.object(window, "add_system_toast") as toast:
            window.composer_context_tags.chips("knowledge")[0].close_btn.click()
        undo = toast.call_args.kwargs["actions"][0][1]
        state.knowledge_refs.append(second)
        undo()
        self.assertEqual(state.knowledge_refs, [second])
        self.assertEqual(state.messages, before_messages)


if __name__ == "__main__":
    unittest.main()
