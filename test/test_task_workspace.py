"""Navigation regressions for the conversation-owned task workspace."""
import json
from pathlib import Path
from unittest.mock import Mock, patch
import unittest

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QToolButton

from core.workbench import WorkbenchError
from ui.task_workspace import TASK_TAB_KEY, task_workspace_view


class TaskWorkspaceTests(unittest.TestCase):
    from test_workspace_presentation import WorkspacePresentationTests as _Fixture
    setUpClass = classmethod(_Fixture.setUpClass.__func__)
    setUp = _Fixture.setUp
    def tearDown(self):
        self.window._subagent_poll.stop()
        self.window.sub_agent_monitor.timer.stop()
        self.assertTrue(QThreadPool.globalInstance().waitForDone(5000))
        self.app.processEvents()
        self._Fixture.tearDown(self)

    def tool(self, tool_id="read-1", result="内容已读取"):
        window = self.window
        window.add_tool_card({"id": tool_id, "name": "read_file", "args": {"path": "需求.md"}})
        window.update_tool_card({"id": tool_id, "result": result})
        return window.get_current_session().tool_cards[tool_id]

    def test_direct_details_back_close_and_reopen_keep_running_and_model_tabs(self):
        window = self.window
        browser = window.browser_workbench
        state = window.get_current_session()
        card = self.tool()
        state.llm_worker = Mock()
        state.llm_worker.isRunning.return_value = False
        messages = list(state.messages)
        tabs = [tab.snapshot() for tab in browser.store.list(state.session_id)]
        window.show_tool_details(card.tool_id, card.args, card.result)
        self.assertIs(window.right_stack.currentWidget(), window.tool_details_tab)
        self.assertTrue(window.observability_segment_control.isHidden())
        self.assertNotIn("工具 ID", window.td_info_label.text())
        self.assertEqual(window.file_tab_strip.active_path, TASK_TAB_KEY)
        window.td_back_btn.click()
        self.assertEqual(window.observability_section_index, window.OBS_SECTION_LOG)
        self.assertFalse(window.observability_segment_control.isHidden())
        window.on_step_item_clicked(window.step_list.item(0))
        window.td_args_toggle.click()
        browser.close_tab(TASK_TAB_KEY)
        self.assertFalse(task_workspace_view(state).opened)
        state.llm_worker.stop.assert_not_called()
        self.assertEqual(state.messages, messages)
        self.assertEqual([tab.snapshot() for tab in browser.store.list(state.session_id)], tabs)
        task_action = next(a for a in window.file_tab_strip.overflow_menu.actions() if a.text() == "任务")
        task_action.trigger()
        self.assertTrue(task_workspace_view(state).opened)
        self.assertFalse(window.td_args_toggle.isChecked())
        self.assertEqual(window.td_result_edit.toPlainText(), card.result)
        self.assertFalse(browser.submission_context(state.session_id)["panel_visible"])
        self.assertFalse(any(t.get("kind") == "task" for t in browser.submission_context(state.session_id)["tabs"]))
        with self.assertRaises(WorkbenchError):
            browser.close_tab(TASK_TAB_KEY, agent=True)
        state.llm_worker = None

    def test_expanded_task_chat_and_file_keep_controls_and_normal_close(self):
        window = self.window
        browser = window.browser_workbench
        window.add_chat_bubble("User", "查看任务进展", animate=False)
        window.input_field.setPlainText("未发送的补充")
        composer = window.input_field
        path = Path(self.temp.name, "说明.txt")
        path.write_text("文件正文", encoding="utf-8")
        window.open_tool_file_from_chat(str(path), window.current_session_id, "read")
        file_tab = browser.active_tab()
        window.open_task_workspace()
        window.set_workspace_expanded(True)
        QTest.qWait(20)
        self.assertTrue(browser.chat_button.isVisible())
        self.assertEqual(browser.chat_button.findChildren(QToolButton), [])
        self.assertFalse(window.content_area.isVisible())
        browser.chat_button.click()
        self.assertTrue(window.content_area.isVisible())
        self.assertIs(window.input_field, composer)
        self.assertEqual(composer.toPlainText(), "未发送的补充")
        browser.activate(TASK_TAB_KEY)
        self.assertFalse(window.content_area.isVisible())
        browser.close_tab(file_tab.id)
        self.assertIs(window.right_stack.currentWidget(), window.tool_details_tab)
        self.assertTrue(task_workspace_view(window.get_current_session()).active)
        window.set_workspace_expanded(False)
        self.assertTrue(window.content_area.isVisible())
        window.set_workspace_expanded(True)
        window.select_workspace_conversation()
        browser.close_tab(TASK_TAB_KEY)
        self.assertIs(window.right_stack.currentWidget(), window.workspace_tab)
        self.assertTrue(window._workspace_chat_selected)
        self.assertTrue(window.content_area.isVisible())
        window.set_workspace_expanded(False)
        self.assertIs(window.right_stack.currentWidget(), window.workspace_tab)

    def test_session_switch_restores_detail_selection_and_failure_without_stale_callback(self):
        window = self.window
        first = window.get_current_session()
        payload = {"text": "完整结果\n" * 300}
        window.show_tool_details("first-call", {"query": "A"}, payload)
        window.td_info_toggle.setChecked(True)
        cursor = window.td_result_edit.textCursor()
        cursor.setPosition(11)
        cursor.setPosition(26, QTextCursor.KeepAnchor)
        window.td_result_edit.setTextCursor(cursor)
        first.drawer_open = True
        first.drawer_tab = window.RIGHT_TAB_OBSERVABILITY
        window.create_new_session()
        second = window.get_current_session()
        self.assertEqual(window.td_result_edit.toPlainText(), "")
        window._show_run_failure_details(second.session_id, {"summary": "请求失败", "error": "连接中断"})
        window.activate_session(first.session_id)
        self.assertEqual(json.loads(window.td_result_edit.toPlainText()), payload)
        self.assertEqual(window.td_result_edit.textCursor().anchor(), 11)
        self.assertEqual(window.td_result_edit.textCursor().position(), 26)
        window.activate_session(second.session_id)
        QTest.qWait(30)
        self.assertEqual(window.current_session_id, second.session_id)
        self.assertEqual(window.td_info_label.text(), "请求失败")
        self.assertEqual(window.td_result_edit.toPlainText(), "连接中断")
        window.copy_tool_arguments()
        self.assertIn("category", self.app.clipboard().text())

    def test_subagent_navigation_keeps_separate_drafts_across_tabs_and_sessions(self):
        window = self.window
        first = window.get_current_session()
        panel = window.sub_agent_monitor
        # Routing and draft ownership use the real panel; no model or daemon needed.
        with patch.object(panel, "_request"):
            window._open_subagent_panel("one")
            panel.editor.setPlainText("给第一个子任务")
            self.assertTrue(window.observability_segment_control.isHidden())
            window.browser_workbench.close_tab(TASK_TAB_KEY)
            window.open_task_workspace()
            self.assertEqual(panel.agent_id, "one")
            self.assertEqual(panel.editor.toPlainText(), "给第一个子任务")
            panel.back.click()
            self.assertFalse(window.observability_segment_control.isHidden())
            panel.open_agent("two")
            panel.editor.setPlainText("给第二个子任务")
            window.create_new_session()
            window._open_subagent_panel("one")
            self.assertEqual(panel.editor.toPlainText(), "")
            panel.editor.setPlainText("另一个会话")
            window.activate_session(first.session_id)
            QTest.qWait(20)
            self.assertEqual(panel.agent_id, "two")
            self.assertEqual(panel.editor.toPlainText(), "给第二个子任务")
            panel.open_agent("one")
            self.assertEqual(panel.editor.toPlainText(), "给第一个子任务")

    def test_reopening_closed_details_refreshes_completed_tool_and_full_copy(self):
        window = self.window
        window.add_tool_card({"id": "running", "name": "run_code", "args": {"code": "print('示例')", "timeout": 5}})
        card = window.get_current_session().tool_cards["running"]
        window.show_tool_details("running", card.args, card.result)
        window.browser_workbench.close_tab(TASK_TAB_KEY)
        window.update_tool_card({"id": "running", "result": '{"ok":false,"error":"运行失败"}'})
        window.open_task_workspace()
        self.assertEqual(window.td_status_label.text(), "失败")
        self.assertIn("运行失败", window.td_result_edit.toPlainText())
        window.copy_tool_arguments()
        self.assertEqual(json.loads(self.app.clipboard().text()), {"code": "print('示例')", "timeout": 5})
        window.copy_tool_result()
        self.assertEqual(json.loads(self.app.clipboard().text())["ok"], False)

    def test_call_list_returns_to_position_after_drilldown_and_session_switch(self):
        window = self.window
        first = window.get_current_session()
        first.step_records = [dict(tool_id=f"call-{i}", display_title=f"读取资料 {i}",
                                   status="done", summary="完整调用结果已保留") for i in range(60)]
        window.refresh_step_list()
        window.open_task_workspace(window.OBS_SECTION_LOG)
        QTest.qWait(30)
        window.step_list.setCurrentRow(40)
        window.step_list.scrollToItem(window.step_list.item(40))
        QTest.qWait(30)
        scroll = window.step_list.verticalScrollBar().value()
        self.assertGreater(scroll, 0)
        window.show_tool_details("call-40", {}, "完整结果")
        window.td_back_btn.click()
        self.assertEqual(window.step_list.verticalScrollBar().value(), scroll)
        window.create_new_session()
        window.open_task_workspace()
        window.activate_session(first.session_id)
        QTest.qWait(40)
        self.assertEqual(window.step_list.currentRow(), 40)
        self.assertEqual(window.step_list.verticalScrollBar().value(), scroll)


if __name__ == "__main__":
    unittest.main()
