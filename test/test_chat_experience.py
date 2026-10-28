import base64
import copy
import os
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")
from PySide6.QtCore import QMimeData, Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QMenu, QWidget, QVBoxLayout, QLabel
from core.conversation_render import build_conversation_render_spans
from core.llm.providers import _build_openai_user_content
from core.workbench_terminal import terminal_launch_args
from core.theme import DesignTokens, default_design_tokens
from core.theme_service import resolve_theme
from main import MainWindow, ComposerContextChip, render_markdown_or_html_with_cache
from ui.conversation_search import find_conversation_messages
from ui.primitives import ProductActionRow, ProductPopover, SidebarInlineNameEditor


class ChatExperienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cowork-chat-test-")
        self.addCleanup(self.temp.cleanup)
        for name in ("core.config_manager.get_app_data_dir", "core.config_manager.get_base_dir", "main.get_app_data_dir"):
            self.enterContext(patch(name, return_value=self.temp.name))
        self.window = MainWindow()
        self.window._startup_hydration_scheduled = True
        self.window._background_services_scheduled = True

    def tearDown(self):
        self.window.close()
        deadline = time.monotonic() + 5
        while not getattr(self.window, "_checkpoint_close_ready", False) and time.monotonic() < deadline:
            self.app.processEvents()
            # Yield Python execution to the persistence workers as well as Qt.
            time.sleep(0.01)
        self.assertTrue(getattr(self.window, "_checkpoint_close_ready", False))
        self.window.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_long_paste_attachment_preserves_exact_text_and_session(self):
        text = "完整原文🚀\r\n" * 1000
        self.window.input_field.setPlainText("请总结附件")
        mime = QMimeData()
        mime.setText(text)
        self.window.input_field.insertFromMimeData(mime)
        self.assertEqual(self.window.input_field.toPlainText(), "请总结附件")
        state = self.window.get_current_session()
        path, = state.prompt_files
        self.assertEqual(Path(path).read_bytes().decode("utf-8"), text)
        wire = _build_openai_user_content("请总结", [{"type": "input_file", "path": path, "name": Path(path).name}])
        self.assertIn("完整原文", str(wire))
        old_id = state.session_id
        self.window.create_new_session()
        self.assertEqual(self.window._current_prompt_files(), [])
        self.window.activate_session(old_id)
        self.assertEqual(self.window._current_prompt_files(), [path])
        self.window._remove_prompt_file(path)
        self.assertFalse(Path(path).exists())

    def test_failed_paste_keeps_original_and_does_not_attach(self):
        text = "不能丢失" * 1100
        mime = QMimeData()
        mime.setText(text)
        with patch("main.os.replace", side_effect=OSError("disk error")):
            self.window.input_field.insertFromMimeData(mime)
        self.assertEqual(self.window.input_field.toPlainText(), text)
        self.assertEqual(self.window._current_prompt_files(), [])

    def test_short_paste_is_inline(self):
        mime = QMimeData()
        mime.setText("一段短文字")
        self.window.input_field.insertFromMimeData(mime)
        self.assertEqual(self.window.input_field.toPlainText(), "一段短文字")
        self.assertFalse(self.window._current_prompt_files())

    def test_session_activation_runs_once_and_keeps_draft(self):
        previous = self.window.current_session_id
        self.window.create_new_session()
        state = self.window.get_current_session()
        with patch.object(self.window, "save_chat_history") as save:
            self.window.input_field.setPlainText("草稿必须保留")
            self.assertEqual(state.composer_draft, "草稿必须保留")
            save.assert_not_called()
        with patch.object(self.window, "set_current_session", wraps=self.window.set_current_session) as activate:
            self.window.activate_session(previous)
        self.assertEqual(activate.call_count, 1)
        self.assertEqual(state.composer_draft, "草稿必须保留")

    def test_skill_click_adds_immediately_and_tag_removal_is_session_local(self):
        self.window.skill_manager_ready = True
        self.window._available_session_skills = lambda: [{"name": "report", "display_name": "报告", "type": "ai_generated"}]
        self.window.show()
        self.window.open_session_skill_picker()
        popover = self.window.session_skill_popover
        QTest.qWait(150)
        item = popover.skill_list.item(0)
        QTest.mouseClick(popover.skill_list.viewport(), Qt.LeftButton,
                         pos=popover.skill_list.visualItemRect(item).center())
        state = self.window.get_current_session()
        self.assertEqual(state.selected_skill_names, ["report"])
        QTest.keyClick(popover.skill_list, Qt.Key_Space)
        self.assertEqual(state.selected_skill_names, [])
        QTest.keyClick(popover.skill_list, Qt.Key_Space)
        popover.close()
        self.app.processEvents()
        self.window.create_new_session()
        self.assertFalse(self.window.composer_context_tags.chips("skills"))
        self.window.activate_session(state.session_id)
        chip = self.window.composer_context_tags.chips("skills")[0]
        self.assertIsInstance(chip, ComposerContextChip)
        QTest.mouseClick(chip.close_btn, Qt.LeftButton)
        self.assertEqual(state.selected_skill_names, [])
        self.assertFalse(self.window.composer_context_tags.chips("skills"))

    def test_permission_choices_persist_and_restore_on_save_failure(self):
        initial = self.window.composer_permission.isChecked()
        self.window._choose_composer_permission(not initial)
        self.assertEqual(self.window.config_manager.get_god_mode(), not initial)
        with patch.object(self.window.config_manager, "set_god_mode", side_effect=OSError("read only")):
            self.window._choose_composer_permission(initial)
        self.assertEqual(self.window.composer_permission.isChecked(), not initial)
        self.assertEqual(self.window.permission_button.text(), "完全访问" if not initial else "请求批准")

    def test_composer_directory_reuses_project_switch_and_stays_visible_when_locked(self):
        project = Path(self.temp.name, "用于验证完整目录提示的项目名称" * 3)
        project.mkdir()
        self.window.config_manager.upsert_project(str(project))
        self.window.input_field.setPlainText("保留这段草稿")
        state = self.window.get_current_session()
        self.window.resize(740, 620)
        self.window.show()
        self.app.processEvents()
        button = self.window.project_selector_btn
        self.assertTrue(button.isVisible())
        self.assertTrue(button.isEnabled())
        menu = QMenu(self.window)
        positions = []

        def choose_project(position):
            positions.append(position)
            next(action for action in menu.actions() if action.data() == str(project)).trigger()

        menu.exec = choose_project
        with patch("main.create_styled_menu", return_value=menu):
            button.click()
        self.assertEqual(positions, [button.mapToGlobal(button.rect().bottomLeft())])
        self.assertEqual(state.workspace_dir, str(project))
        self.assertEqual(state.workspace_source, "project")
        self.assertEqual(self.window.input_field.toPlainText(), "保留这段草稿")
        self.assertIn(str(project), button.toolTip())
        self.assertIn(project.name, button.accessibleName())
        self.window.sync_conversation_widths()
        self.app.processEvents()
        self.assertLessEqual(button.mapTo(self.window, button.rect().bottomRight()).x(),
                             self.window.input_card.mapTo(self.window, self.window.input_card.rect().bottomRight()).x())
        self.assertGreater(button.mapTo(self.window, button.rect().topLeft()).y(),
                           self.window.input_card.mapTo(self.window, self.window.input_card.rect().bottomLeft()).y())
        state.messages = [{"id": "workspace-u", "role": "user", "content": "已有消息"}]
        self.window.normalize_session_ui(state)
        self.assertTrue(button.isVisible())
        self.assertFalse(button.isEnabled())
        self.assertIn("已有内容", button.toolTip())
        self.assertFalse(self.window.select_project_for_current_conversation(""))
        self.window.create_new_session()
        self.assertIn("聊天工作目录", button.text())
        self.window.activate_session(state.session_id)
        self.assertIn(str(project), button.toolTip())
        self.assertFalse(button.isEnabled())

    def test_plus_conversation_reference_keeps_draft_and_uses_existing_snapshot(self):
        self.window.chat_storage.save_conversation("source", [
            {"id": "source-u", "role": "user", "content": "需要引用的规划原文"},
        ], title="历史规划")
        self.window.input_field.setPlainText("请结合历史对话，继续补充验收标准。")
        cursor = self.window.input_field.textCursor()
        cursor.setPosition(6)
        self.window.input_field.setTextCursor(cursor)
        self.window.show()
        self.window.show_prompt_tool_menu()
        menu = self.window.composer_action_popover
        row = next(row for row in menu.findChildren(ProductActionRow) if row.title_label.text() == "对话")
        QTest.mouseClick(row.title_label, Qt.LeftButton)
        self.app.processEvents()
        picker = self.window.conversation_mention_popover
        self.assertTrue(picker.isVisible())
        self.assertTrue(picker.tabs.isHidden())
        picker.search.setText("历史规划")
        QTest.mouseClick(picker.items.viewport(), Qt.LeftButton,
                         pos=picker.items.visualItemRect(picker.items.item(0)).center())
        state = self.window.get_current_session()
        self.assertEqual(state.conversation_refs, [{"session_id": "source", "title": "历史规划"}])
        self.assertEqual(self.window.input_field.toPlainText(), "请结合历史对话，继续补充验收标准。")
        self.assertEqual(self.window.input_field.textCursor().position(), 6)
        self.assertTrue(self.window.composer_context_tags.chips("references")[0].isVisible())
        prepared = self.window.prepare_conversation_references(state, state.conversation_refs)
        self.assertIn("需要引用的规划原文", Path(prepared[0]["snapshot_path"]).read_text(encoding="utf-8"))
        self.window.show_conversation_reference_picker()
        picker = self.window.conversation_mention_popover
        picker.choose(picker.items.item(0))
        self.assertEqual(len(state.conversation_refs), 1)
        self.window.create_new_session()
        self.assertEqual(self.window.get_current_session().conversation_refs, [])
        self.assertFalse(self.window.composer_context_tags.chips("references"))
        self.window.activate_session(state.session_id)
        self.assertTrue(self.window.composer_context_tags.chips("references")[0].isVisible())
        self.window.remove_conversation_reference(state.session_id, "source")
        self.assertEqual(state.conversation_refs, [])

    def test_cascading_menu_hover_switch_keyboard_and_outside_close(self):
        self.window.skill_manager_ready = True
        self.window._available_session_skills = lambda: [
            {"name": "report", "display_name": "报告", "type": "ai_generated"},
            {"name": "docs", "display_name": "文档连接器", "source_format": "mcp_server"},
        ]
        self.window.resize(860, 720)
        self.window.show()
        self.app.processEvents()
        self.window.show_prompt_tool_menu()
        menu = self.window.composer_action_popover
        rows = {row.title_label.text(): row for row in menu.findChildren(ProductActionRow)}
        QTest.keyClick(menu, Qt.Key_Down)
        self.assertIs(self.app.focusWidget(), rows["模式"])
        original_position = menu.pos()
        QTest.mouseMove(rows["技能"], rows["技能"].rect().center())
        QTest.qWait(150)
        self.assertIsNone(menu._child_popover)
        QTest.qWait(220)
        picker = menu._child_popover
        self.assertIsNotNone(picker)
        self.assertTrue(menu.isVisible())
        self.assertTrue(self.window.rect().contains(picker.geometry()))
        self.assertFalse(menu.geometry().intersects(picker.geometry()))
        self.assertEqual(menu.pos(), original_position)
        self.window.resize(740, 620)
        QTest.qWait(100)
        self.assertTrue(self.window.rect().contains(menu.geometry()))
        self.assertTrue(self.window.rect().contains(picker.geometry()))
        picker.search_input.setText("报告")
        QTest.mouseClick(picker.search_input, Qt.LeftButton)
        self.assertTrue(menu.isVisible())
        QTest.mouseClick(rows["连接器"], Qt.LeftButton)
        connector = menu._child_popover
        self.assertIsNot(connector, picker)
        self.assertEqual(connector.skill_list.item(0).data(Qt.UserRole), "docs")
        QTest.keyClick(connector.search_input, Qt.Key_Escape)
        self.assertTrue(menu.isVisible())
        self.assertIsNone(menu._child_popover)
        QTest.keyClick(rows["模式"], Qt.Key_Right)
        self.assertIsNotNone(menu._child_popover)
        QTest.mouseClick(self.window.input_field.viewport(), Qt.LeftButton)
        self.assertFalse(menu.isVisible())
        self.window.show_prompt_tool_menu()
        menu = self.window.composer_action_popover
        rows = {row.title_label.text(): row for row in menu.findChildren(ProductActionRow)}
        QTest.keyClick(rows["模式"], Qt.Key_Right)
        child = menu._child_popover
        self.app.sendEvent(self.window, QEvent(QEvent.WindowDeactivate))
        self.assertFalse(menu.isVisible())
        self.assertFalse(child.isVisible())

    def test_composer_menu_reopens_and_releases_closed_popovers(self):
        self.window.show()
        self.app.processEvents()
        persistent_popovers = self.window.findChildren(ProductPopover)
        for _ in range(3):
            QTest.mouseClick(self.window.tool_menu_btn, Qt.LeftButton)
            menu = self.window.composer_action_popover
            self.assertTrue(menu.isVisible())
            rows = {row.title_label.text(): row for row in menu.findChildren(ProductActionRow)}
            QTest.keyClick(rows["模式"], Qt.Key_Right)
            self.assertIsNotNone(menu._child_popover)
            QTest.mouseClick(self.window.input_field.viewport(), Qt.LeftButton)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()
            self.assertIsNone(self.window.composer_action_popover)
            self.assertEqual(self.window.findChildren(ProductPopover), persistent_popovers)
        self.window.show_prompt_tool_menu()
        menu = self.window.composer_action_popover
        self.app.sendEvent(self.window, QEvent(QEvent.WindowDeactivate))
        self.assertFalse(menu.isVisible())

    def test_selected_expert_chip_is_visible_removable_and_session_local(self):
        profile = {"id": "qa-expert", "name": "产品规划与需求分析专家", "description": "规划产品"}
        self.window._available_agent_profiles = lambda: [profile]
        self.window.resize(740, 620)
        self.window.show()
        self.app.processEvents()
        chip = self.window.agent_picker_btn
        self.assertTrue(chip.isVisible())
        self.assertFalse(chip.close_btn.isVisible())
        self.assertTrue(self.window.select_conversation_agent(profile))
        self.window.sync_conversation_widths()
        self.app.processEvents()
        self.assertTrue(chip.isVisible())
        self.assertTrue(chip.close_btn.isVisible())
        self.assertIn(profile["name"], chip.toolTip())
        for control in (chip, self.window.model_select_btn, self.window.action_btn):
            self.assertLess(control.mapTo(self.window.input_card, control.rect().bottomRight()).x(),
                            self.window.input_card.width())
        self.assertLess(chip.mapTo(self.window, chip.rect().bottomRight()).x(),
                        self.window.model_select_btn.mapTo(self.window, self.window.model_select_btn.rect().topLeft()).x())
        state = self.window.get_current_session()
        with patch.object(self.window, "_session_is_busy", return_value=True):
            self.window.refresh_conversation_agent_controls()
            self.assertTrue(chip.isVisible())
            self.assertFalse(chip.isEnabled())
        self.window.create_new_session()
        self.assertTrue(chip.isVisible())
        self.assertFalse(chip.close_btn.isVisible())
        self.window.activate_session(state.session_id)
        self.assertTrue(chip.isVisible())
        self.assertTrue(chip.isEnabled())
        QTest.mouseClick(chip.main_btn, Qt.LeftButton)
        self.assertIs(self.window.conversation_agent_popover._anchor, chip)
        self.window.conversation_agent_popover.close()
        QTest.mouseClick(chip.close_btn, Qt.LeftButton)
        self.assertFalse(state.conversation_agent)
        self.assertTrue(chip.isVisible())
        self.assertFalse(chip.close_btn.isVisible())

    def test_expert_row_applies_immediately_and_reopens_checked(self):
        profile = {"id": "qa", "name": "需求分析", "description": "整理目标与验收条件"}
        self.window._available_agent_profiles = lambda: [profile]
        self.window.show()
        self.app.processEvents()
        state = self.window.get_current_session()
        self.window.show_conversation_agent_picker()
        picker = self.window.conversation_agent_popover
        self.assertTrue(picker.apply_btn.isHidden())
        QTest.mouseClick(picker.items.viewport(), Qt.LeftButton,
                         pos=picker.items.visualItemRect(picker.items.item(0)).center())
        self.assertEqual(state.conversation_agent["id"], "qa")
        self.assertFalse(picker.isVisible())
        self.window.show_conversation_agent_picker()
        picker = self.window.conversation_agent_popover
        self.assertEqual(picker.items.item(0).checkState(), Qt.Checked)
        QTest.mouseClick(picker.clear_btn, Qt.LeftButton)
        self.assertFalse(state.conversation_agent)

    def test_running_primary_action_tracks_text_paste_files_and_stop(self):
        state = self.window.get_current_session()
        state.session_status = "running"
        state.turn_steerable = True
        try:
            self.window.show()
            self.window.normalize_session_ui(state)
            self.assertEqual(self.window.action_btn.property("composerAction"), "stop")
            with patch.object(self.window, "stop_agent") as stop, patch.object(self.window, "handle_send") as send:
                self.window.action_btn.click()
                stop.assert_called_once()
                send.assert_not_called()
                self.window.on_action_clicked()
                self.assertEqual(stop.call_count, 1)  # Enter never becomes stop.
                send.reset_mock()
                self.window.input_field.setPlainText("补充验收条件")
                self.assertEqual(self.window.action_btn.text(), "发送补充")
                self.window.action_btn.click()
                send.assert_called_once()
                self.window.input_field.clear()
                self.assertEqual(self.window.action_btn.property("composerAction"), "stop")
                mime = QMimeData()
                mime.setText("长文本附件\n" * 1000)
                self.window.input_field.insertFromMimeData(mime)
                self.assertEqual(self.window.action_btn.property("composerAction"), "send")
                self.window._apply_runtime_theme()
                self.assertEqual(self.window.action_btn.property("composerAction"), "send")
                self.window._remove_prompt_file(state.prompt_files[0])
                self.assertEqual(self.window.action_btn.property("composerAction"), "stop")
                self.window.input_field.setPlainText("保留的草稿")
                self.window.show_prompt_tool_menu()
                menu = self.window.composer_action_popover
                row = next(row for row in menu.findChildren(ProductActionRow) if row.title_label.text() == "停止当前任务")
                QTest.mouseClick(row, Qt.LeftButton)
                self.app.processEvents()
                self.assertEqual(stop.call_count, 2)
                self.assertEqual(self.window.input_field.toPlainText(), "保留的草稿")
            state.stop_pending = True
            self.window.refresh_composer_action_state()
            self.assertFalse(self.window.action_btn.isEnabled())
            self.assertEqual(self.window.action_btn.text(), "正在停止")
        finally:
            state.stop_pending = False
            state.session_status = "idle"
            state.turn_steerable = False
            self.window.normalize_session_ui(state)

    def test_conversation_picker_ignores_stale_session_and_can_retry_load_failure(self):
        self.window.chat_storage.upsert_conversation("source", title="已有对话")
        self.window.input_field.setPlainText("保留草稿")
        self.window.show()
        state = self.window.get_current_session()
        with patch.object(self.window.chat_storage, "list_conversation_summaries", side_effect=OSError("read failed")), \
             patch.object(self.window, "add_system_toast") as toast:
            self.window.show_conversation_reference_picker()
        self.assertIn("输入已保留", toast.call_args.args[0])
        self.assertEqual(self.window.input_field.toPlainText(), "保留草稿")
        self.window.show_conversation_reference_picker()
        picker = self.window.conversation_mention_popover
        record = picker.items.item(0).data(Qt.UserRole)[1]
        self.window.create_new_session()
        picker.selected.emit("会话", record)
        self.assertEqual(state.conversation_refs, [])
        self.assertEqual(self.window.get_current_session().conversation_refs, [])

    def test_at_reference_still_consumes_only_trigger_and_ignores_changed_draft(self):
        self.window.chat_storage.upsert_conversation("source", title="已有对话")
        self.window.show()
        self.window.input_field.setPlainText("name@example.com")
        self.window.input_field.moveCursor(QTextCursor.End)
        with patch.object(self.window, "_show_conversation_picker") as show:
            self.window.show_conversation_mention_menu()
            show.assert_not_called()
        self.window.input_field.setPlainText("请参考 @")
        self.window.input_field.moveCursor(QTextCursor.End)
        self.window.show_conversation_mention_menu()
        picker = self.window.conversation_mention_popover
        picker.choose(picker.items.item(0))
        state = self.window.get_current_session()
        self.assertEqual(self.window.input_field.toPlainText(), "请参考 ")
        self.assertEqual(state.conversation_refs[0]["session_id"], "source")
        self.window.remove_conversation_reference(state.session_id, "source")
        self.window.input_field.setPlainText("请参考 @")
        self.window.input_field.moveCursor(QTextCursor.End)
        self.window.show_conversation_mention_menu()
        picker = self.window.conversation_mention_popover
        self.window.input_field.setPlainText("已修改草稿")
        picker.choose(picker.items.item(0))
        self.assertEqual(self.window.input_field.toPlainText(), "已修改草稿")
        self.assertEqual(state.conversation_refs, [])

    def test_skill_and_connector_categories_preserve_each_others_selection(self):
        self.window.skill_manager_ready = True
        self.window._available_session_skills = lambda: [
            {"name": "report", "type": "ai_generated"},
            {"name": "docs", "source_format": "mcp_server"},
        ]
        self.window.show()
        self.window.set_session_selected_skills(["report"])
        self.window.open_session_skill_picker(category="connectors")
        picker = self.window.session_skill_popover
        self.assertEqual([skill["name"] for skill in picker.skills], ["docs"])
        picker.skill_list.item(0).setCheckState(Qt.Checked)
        self.assertEqual(self.window.get_current_session().selected_skill_names, ["report", "docs"])
        picker.close()
        self.window.open_session_skill_picker()
        picker = self.window.session_skill_popover
        self.assertEqual([skill["name"] for skill in picker.skills], ["report"])
        picker.skill_list.item(0).setCheckState(Qt.Unchecked)
        self.assertEqual(self.window.get_current_session().selected_skill_names, ["docs"])
        picker.close()

    def test_mode_switch_uses_existing_grill_state_and_ignores_stale_session(self):
        self.window.show()
        state = self.window.get_current_session()
        self.window.show_composer_mode_picker()
        choices = self.window.composer_mode_choices
        QTest.mouseClick(choices.viewport(), Qt.LeftButton, pos=choices.visualItemRect(choices.item(1)).center())
        self.assertEqual(state.grill_mode_state, "armed")
        self.window.create_new_session()
        choices.itemClicked.emit(choices.item(0))
        self.assertEqual(state.grill_mode_state, "armed")
        self.assertEqual(self.window.get_current_session().grill_mode_state, "disabled")

    def test_settings_ignore_navigation_config_and_refresh_common_preferences_in_place(self):
        self.window.open_settings()
        page = self.window.product_pages["settings"]
        self.window.show_product_page("knowledge")
        self.window.config_manager.config["recent_workspaces"] = ["different-workspace"]
        self.window.config_manager.config["file_navigator_scope"] = "workspace"
        self.window.config_manager.config["favorite_run_history"] = [{"status": "completed"}]
        self.window.open_settings()
        self.assertIs(page, self.window.product_pages["settings"])
        self.window.show_conversation_page()
        self.window.config_manager.config["god_mode"] = not self.window.config_manager.get_god_mode()
        self.window.open_settings()
        self.assertIs(page, self.window.product_pages["settings"])
        self.assertEqual(page.god_mode_check.isChecked(), self.window.config_manager.get_god_mode())
        self.assertFalse(page._settings_dirty)

    def test_settings_reloads_changed_complex_configuration(self):
        self.window.open_settings()
        page = self.window.product_pages["settings"]
        self.window.show_conversation_page()
        self.window.config_manager.config["data_security"] = {"enabled": True}
        self.window.open_settings()
        self.assertIsNot(page, self.window.product_pages["settings"])
        self.assertTrue(self.window.product_pages["settings"].data_security_panel.state()["enabled"])

    def test_pristine_views_and_legacy_disabled_mode_do_not_create_history(self):
        for _ in range(3):
            state = self.window.get_current_session()
            state.persisted_conversation_meta = {"grill_mode_state": "disabled", "update_composer": {"text": "", "files": []}}
            self.assertTrue(self.window.save_chat_history(state.session_id, flush=True))
            self.assertIsNone(self.window.chat_storage.get_conversation_record(state.session_id))
            self.window.create_new_session()

    def test_real_draft_restores_text_attachments_and_references_then_clears_without_resurrection(self):
        state = self.window.get_current_session()
        self.window.input_field.setPlainText("需要继续整理的草稿")
        state.prompt_files = [str(Path(self.temp.name, "attachment.txt"))]
        Path(state.prompt_files[0]).write_text("附件", encoding="utf-8")
        state.conversation_refs = [{"session_id": "reference", "title": "参考对话"}]
        self.assertTrue(self.window.save_chat_history(state.session_id, flush=True))
        saved = self.window.chat_storage.get_conversation_meta(state.session_id)
        self.assertEqual(saved["composer_draft"]["text"], "需要继续整理的草稿")
        self.assertEqual(saved["composer_draft"]["files"], state.prompt_files)
        state.composer_draft = ""
        state.prompt_files = []
        state.conversation_refs = []
        self.window.input_field.clear()
        state.history_loaded = False
        self.window.queue_session_history_load(state.session_id)
        deadline = time.monotonic() + 4
        while not state.history_loaded and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertTrue(state.history_loaded)
        self.assertEqual(state.composer_draft, "需要继续整理的草稿")
        self.assertEqual(state.prompt_files, saved["composer_draft"]["files"])
        self.assertEqual(state.conversation_refs, saved["composer_conversation_refs"])
        self.window.input_field.clear()
        state.composer_draft = ""
        state.prompt_files = []
        state.conversation_refs = []
        self.assertTrue(self.window.save_chat_history(state.session_id, flush=True))
        cleared = self.window.chat_storage.get_conversation_meta(state.session_id)
        self.assertNotIn("composer_draft", cleared)
        self.assertNotIn("composer_conversation_refs", cleared)
        self.assertIsNone(self.window._build_chat_save_request(state))
        self.window._render_history_list(self.window.chat_storage.list_conversations())
        self.assertNotIn(state.session_id, self.window.history_rows)

    def test_update_barrier_saves_real_draft_and_skips_blank_view(self):
        blank_id = self.window.current_session_id
        self.window.create_new_session()
        state = self.window.get_current_session()
        self.window.input_field.setPlainText("更新后继续")
        self.window.sync_current_session_state()
        self.window._update_preparing = True
        self.window._update_transaction = self.temp.name
        self.window._update_installer_process = SimpleNamespace(poll=lambda: None)
        self.window._update_checkpoint_queued = False
        self.window._update_deadline = time.monotonic() + 30
        with patch.object(self.window, "_commit_update_exit") as commit, patch.object(self.window, "_abort_update") as abort, patch("ui.update_lifecycle.QTimer.singleShot"):
            self.window._await_update_saves()
            self.window._await_update_saves()
            abort.assert_not_called()
            commit.assert_called_once()
        self.window._update_preparing = False
        self.assertIsNone(self.window.chat_storage.get_conversation_record(blank_id))
        self.assertEqual(self.window.chat_storage.get_conversation_meta(state.session_id)["composer_draft"]["text"], "更新后继续")

    def test_first_submission_consumes_saved_composer_without_changing_input_before_commit(self):
        state = self.window.get_current_session()
        state.composer_draft = "准备发送"
        state.prompt_files = ["attachment.txt"]
        message = {"id": "u1", "role": "user", "content": "准备发送"}
        request = self.window._build_chat_save_request(state, messages=[message], consume_reference_draft=True)
        self.assertNotIn("composer_draft", request.meta)
        self.assertEqual(state.composer_draft, "准备发送")
        self.assertEqual(request.messages, [message])

    def test_sent_draft_title_survives_sidebar_resize_and_session_switch(self):
        self.window._model_onboarding_shown = True
        self.window.resize(1200, 800)
        self.window.show()
        state = self.window.get_current_session()
        self.window.input_field.setPlainText("请同时使用所选能力分析")
        self.assertTrue(self.window.save_chat_history(state.session_id, flush=True))
        self.window.refresh_history_list()
        self.app.processEvents()
        row = self.window.history_rows[state.session_id]
        button = self.window.history_buttons[state.session_id]
        self.assertTrue(button.text().startswith("草稿"))

        title = "分析下今天的A股"
        self.window.input_field.setPlainText(title)
        with patch.object(self.window, "_model_profile_for_state", return_value={"id": "test-model"}), \
                patch.object(self.window, "_selected_model_supports_vision", return_value=False), \
                patch.object(self.window, "_ensure_vision_attachment_support", return_value=True), \
                patch.object(self.window, "_build_run_context", return_value={}), \
                patch.object(self.window, "queue_daemon_connection"), \
                patch.object(self.window, "process_agent_logic"), \
                patch.object(self.window, "refresh_history_list") as rebuild:
            self.assertTrue(self.window._submit_session_request(state, title, [], clear_current_input=True))
            self.assertTrue(self.window.flush_pending_chat_saves(session_id=state.session_id, timeout_ms=3000))
            self.app.processEvents()
            for width in (90, 220, 140):
                button.setFixedWidth(width)
                self.app.processEvents()
                self.assertFalse(button.text().startswith("草稿"), button.text())
            button.setMinimumWidth(0)
            button.setMaximumWidth(16777215)
            self.assertEqual(button.toolTip(), title)
            self.assertEqual(self.window.workspace_title_label.text(), title)
            self.assertIs(self.window.history_rows[state.session_id], row)
            rebuild.assert_not_called()

        record = self.window.chat_storage.get_conversation_record(state.session_id)
        self.assertEqual(record["title"], title)
        self.assertEqual(self.window.chat_storage.get_messages(state.session_id)[-1]["content"], title)
        self.window.create_new_session()
        self.window.activate_session(state.session_id)
        self.app.processEvents()
        self.assertEqual(self.window.history_buttons[state.session_id].toolTip(), title)
        self.assertFalse(self.window.history_buttons[state.session_id].text().startswith("草稿"))

    def test_home_recent_uses_message_activity_instead_of_metadata_write_time(self):
        records = [
            {"id": "old", "title": "旧对话", "updated_at": 300, "last_message_at": 100, "has_messages": True, "meta": {}},
            {"id": "new", "title": "新对话", "updated_at": 200, "last_message_at": 200, "has_messages": True, "meta": {}},
        ]
        self.window._render_history_list(records)
        self.assertEqual([record["id"] for record in self.window._home_recent_records], ["new", "old"])
        self.assertEqual(self.window.home_page.recent_buttons[0].property("sessionId"), "new")

    def test_search_loads_old_pages_and_highlights_matching_answer(self):
        state = self.window.get_current_session()
        self.window.clear_chat_layout(state.chat_layout)
        state.empty_state = None
        state.messages = [
            {"id": f"{role}{i}", "role": role, "content": f"{'目标答案' if i == 0 and role == 'assistant' else '普通文字'} {i}"}
            for i in range(8) for role in ("user", "assistant")
        ]
        state.render_items = build_conversation_render_spans(state.messages)
        state.history_loaded = True
        state.displayed_render_count = 2
        self.window._render_session_history_spans(state, state.render_items[-2:])
        self.window.resize(1280, 800)
        self.window.show()
        self.window._sync_home_presentation()
        self.window._open_conversation_search()
        self.window.conversation_search.input.setText("目标答案")
        for _ in range(100):
            QTest.qWait(50)
            if self.window._conversation_search_highlight is not None:
                break
        self.assertEqual(len(self.window._conversation_search_matches), 1)
        self.assertIn("assistant0", state.render_node_by_message_id)
        self.assertIsNotNone(self.window._conversation_search_highlight)
        self.assertIn("目标答案", self.window._conversation_search_highlight.toPlainText())
        self.window.create_new_session()
        self.assertTrue(self.window.conversation_search.isHidden())
        self.assertEqual(self.window._conversation_search_matches, [])

    def test_header_unchanged_title_does_not_write(self):
        state = self.window.get_current_session()
        with patch.object(self.window.chat_storage, "upsert_conversation") as save:
            self.window.begin_session_inline_rename(state.session_id, (self.window.workspace_title_layout, self.window.workspace_title_label))
            editor = self.window.workspace_title_layout.parentWidget().findChild(SidebarInlineNameEditor)
            editor._commit()
            save.assert_not_called()


class ChatExperienceUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_click_on_nonfocus_surface_commits_once(self):
        host = QWidget()
        layout = QVBoxLayout(host)
        editor = SidebarInlineNameEditor("旧标题")
        blank = QLabel("点击空白区域")
        layout.addWidget(editor)
        layout.addWidget(blank)
        results = []
        editor.commitRequested.connect(results.append)
        host.show()
        editor.setFocus()
        editor.setText("新标题")
        self.app.processEvents()
        QTest.mouseClick(blank, Qt.LeftButton)
        QTest.mouseClick(blank, Qt.LeftButton)
        self.assertEqual(results, ["新标题"])
        host.close()

    def test_search_uses_visible_projection_without_modifying_history(self):
        messages = [
            {"id": "u", "role": "user", "content": "Hello 世界"},
            {"id": "a", "role": "assistant", "content": "secret", "meta": {"security_display_content": "HELLO"}},
            {"id": "h", "role": "user", "content": "hello", "meta": {"hidden": True, "kind": "runtime_context"}},
            {"id": "t", "role": "tool", "content": "hello"},
        ]
        original = copy.deepcopy(messages)
        self.assertEqual([m["message_id"] for m in find_conversation_messages(messages, "hello")], ["u", "a"])
        self.assertEqual(find_conversation_messages(messages, "secret"), [])
        self.assertEqual(messages, original)

    def test_link_style_and_cache_follow_theme(self):
        _, light = render_markdown_or_html_with_cache("[文件](C:/report.txt)", final=True)
        with patch.object(DesignTokens, "chat_link", "#8ab4f8"):
            _, dark = render_markdown_or_html_with_cache("[文件](C:/report.txt)", final=True)
        self.assertIn("text-decoration: underline", light)
        self.assertIn("#8ab4f8", dark)
        self.assertNotEqual(light, dark)
        resolved = resolve_theme({"overrides": {"tokens": {"bg_chat": "#202020"}}}, default_design_tokens())
        self.assertEqual(resolved["tokens"]["chat_link"], "#8ab4f8")

    @unittest.skipUnless(os.name == "nt", "PowerShell on Windows")
    def test_prompt_shortens_only_display_and_handles_unicode(self):
        args = terminal_launch_args({"id": "powershell", "args": ["-NoLogo"]})
        script = base64.b64decode(args[-1]).decode("utf-16-le")
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp, "含空格的目录 " + "long-name-" * 8)
            path.mkdir()
            script += "\n[Console]::OutputEncoding = [Text.Encoding]::UTF8; prompt; (Get-Location).Path"
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            result = subprocess.run(["powershell.exe", "-NoProfile", "-EncodedCommand", encoded], cwd=path, capture_output=True, check=True)
            output = result.stdout.decode("utf-8-sig").splitlines()
            self.assertLess(len(output[0]), 55)
            self.assertIn("…", output[0])
            self.assertEqual(output[1], str(path))
