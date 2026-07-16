import os
import hashlib
import inspect
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QVBoxLayout, QWidget

from core.chat_storage import ChatStorage
from core.theme import DesignTokens

from main import (
    AssistantTurnGroup,
    ChatBubble,
    ComposerActionPopover,
    ConversationSkillOptionsDialog,
    ConversationSkillPreviewDialog,
    ConversationSkillRangeDialog,
    InlineInteractionCard,
    InteractionChoiceButton,
    MainWindow,
    ModelSelectorPopover,
    SearchableSkillPickerButton,
    SessionSkillPickerPopover,
    GuidanceTimelineEvent,
    ToolCallCard,
    is_hidden_manual_skill_change_message,
    launch_daemon_subprocess,
)
from ui.primitives import ProductActionRow, ProductPopover


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

    def test_model_picker_viewport_click_emits_selected_model(self):
        host = QWidget()
        host.resize(640, 480)
        anchor = QPushButton("模型", host)
        anchor.setGeometry(300, 420, 80, 32)
        profiles = [
            {"id": "model-a", "display_name": "A", "model_name": "a"},
            {"id": "model-b", "display_name": "B", "model_name": "b"},
        ]
        picker = ModelSelectorPopover(profiles, "model-a", host)
        selected = []
        picker.modelSelected.connect(selected.append)
        host.show()
        self.app.processEvents()
        self.assertTrue(picker.show_for(anchor, prefer_above=True))

        item = picker.model_list.item(1)
        QTest.mouseClick(
            picker.model_list.viewport(),
            Qt.LeftButton,
            pos=picker.model_list.visualItemRect(item).center(),
        )
        self.app.processEvents()

        self.assertEqual(selected, ["model-b"])
        self.assertEqual(picker.selected_id, "model-b")
        host.deleteLater()

    def test_composer_add_popover_has_no_duplicate_agent_entry(self):
        source = inspect.getsource(ComposerActionPopover.__init__)
        for title in ("添加文件", "指定能力", "沉淀为 Skill"):
            self.assertIn(title, source)
        self.assertNotIn("添加智能体", source)

    def test_composer_add_popover_clicks_enabled_action_and_keeps_disabled_action_inert(self):
        host = QWidget()
        host.resize(640, 480)
        anchor = QPushButton("+", host)
        anchor.setGeometry(300, 420, 32, 32)
        hits = []
        state = SimpleNamespace(selected_skill_names=[], messages=[])
        window = SimpleNamespace(
            pending_conversation_skill_result=None,
            skill_manager_ready=True,
            skill_load_error="",
            conversation_skill_worker=None,
            get_current_session=lambda: state,
            _session_is_busy=lambda _state: False,
            select_files_for_prompt=lambda: hits.append("file"),
            open_session_skill_picker=lambda: hits.append("skill"),
            start_conversation_skill_flow=lambda: hits.append("capture"),
        )
        popover = ComposerActionPopover(window, host)
        rows = popover.findChildren(ProductActionRow)
        host.show()
        self.app.processEvents()
        self.assertTrue(popover.show_for(anchor, prefer_above=True))

        QTest.mouseClick(rows[0].title_label, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(hits, ["file"])
        self.assertTrue(popover.isHidden())

        second_popover = ComposerActionPopover(window, host)
        second_rows = second_popover.findChildren(ProductActionRow)
        self.assertFalse(second_rows[-1].isEnabled())
        second_popover.show_for(anchor, prefer_above=True)
        QTest.mouseClick(second_rows[-1].title_label, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(hits, ["file"])
        host.deleteLater()

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

    def test_inline_questionnaire_uses_theme_owned_choice_rows(self):
        card = InlineInteractionCard(
            {
                "kind": "questionnaire",
                "questions": [{
                    "id": "priority",
                    "question": "优先处理什么？",
                    "options": [
                        {"label": "质量", "value": "quality", "description": "先保证正确"},
                        {"label": "自定义", "value": "__custom__"},
                    ],
                }],
            }
        )
        self.assertFalse(card.findChildren(QComboBox))
        choices = card.findChildren(InteractionChoiceButton)
        self.assertEqual(len(choices), 2)
        choices[1].click()
        self.assertTrue(card.question_controls[0]["input"].isEnabled())
        card.deleteLater()

    def test_skill_preview_constructs_with_product_action_bar(self):
        dialog = ConversationSkillPreviewDialog(
            {"skill_name": "demo-skill", "experience_items": ["lesson"]},
            mode="create",
        )
        labels = [button.text() for button in dialog.findChildren(QPushButton)]
        self.assertIn("保存 Skill", labels)
        dialog.deleteLater()

    def test_resolved_inline_request_is_removed_from_conversation(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            values = []
            card = window._show_inline_interaction_request(
                {
                    "request_id": "remove-after-submit",
                    "kind": "choice",
                    "options": [{"label": "继续", "value": "continue"}],
                },
                state.session_id,
                values.append,
            )
            card.option_checks[0].setChecked(True)
            card._submit()
            self.app.processEvents()
            self.assertEqual(values, ["continue"])
            self.assertNotIn("remove-after-submit", state.pending_interactions)
            self.assertEqual(state.chat_layout.indexOf(card), -1)
            self.assertTrue(card.isHidden())
        finally:
            window.close()
            window.deleteLater()

    def test_legacy_interaction_dialog_cannot_create_top_level_surface(self):
        with self.assertRaisesRegex(RuntimeError, "InlineInteractionCard"):
            MainWindow.show_interaction_dialog(object(), {"kind": "text"})

    def test_retiring_first_send_empty_state_does_not_create_window(self):
        host = QWidget()
        layout = QVBoxLayout(host)
        empty_state = QWidget(host)
        layout.addWidget(empty_state)
        state = SimpleNamespace(session_id="first-submit", empty_state=empty_state, chat_layout=layout)
        before = {id(widget) for widget in QApplication.topLevelWidgets() if widget.isVisible()}
        self.assertTrue(MainWindow._retire_session_empty_state(object(), state, reason="test_first_submit"))
        self.app.processEvents()
        after = {id(widget) for widget in QApplication.topLevelWidgets() if widget.isVisible()}
        self.assertIsNone(state.empty_state)
        self.assertEqual(layout.indexOf(empty_state), -1)
        self.assertEqual(after - before, set())
        host.deleteLater()

    def test_daemon_launch_keeps_windows_console_hidden(self):
        with patch("main.subprocess.Popen") as popen:
            launch_daemon_subprocess(23333)
        kwargs = popen.call_args.kwargs
        if os.name == "nt":
            self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
            self.assertTrue(kwargs["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW)

    def test_inline_request_resolution_failure_preserves_input_for_retry(self):
        window = MainWindow()
        try:
            state = window.get_current_session()

            def fail(_value):
                raise RuntimeError("bridge unavailable")

            card = window._show_inline_interaction_request(
                {"request_id": "retry-submit", "kind": "text"},
                state.session_id,
                fail,
            )
            card.text_input.setText("保留这段输入")
            card._submit()
            self.app.processEvents()
            self.assertIn("retry-submit", state.pending_interactions)
            self.assertEqual(card.text_input.text(), "保留这段输入")
            self.assertTrue(card.submit_btn.isEnabled())
            self.assertIn("提交失败", card.validation_label.text())
        finally:
            window.close()
            window.deleteLater()

    def test_product_popover_is_single_in_window_overlay(self):
        host = QWidget()
        host.resize(640, 480)
        anchor = QPushButton("+​", host)
        anchor.setGeometry(300, 420, 32, 32)
        popover = ProductPopover(host, width=260)
        layout = QVBoxLayout(popover)
        layout.addWidget(QLabel("单一操作列表"))
        host.show()
        self.app.processEvents()

        self.assertTrue(popover.show_for(anchor, prefer_above=True))
        self.assertFalse(popover.isWindow())
        self.assertIs(popover.parentWidget(), host)
        self.assertTrue(host.rect().contains(popover.geometry()))
        QTest.mouseClick(host, Qt.LeftButton, pos=QPoint(8, 8))
        self.app.processEvents()
        self.assertTrue(popover.isHidden())
        host.deleteLater()

    def test_product_popover_keeps_native_window_target_click_inside(self):
        host = QWidget()
        host.resize(640, 480)
        anchor = QPushButton("+", host)
        anchor.setGeometry(300, 420, 32, 32)
        popover = ProductPopover(host, width=260)
        layout = QVBoxLayout(popover)
        layout.addWidget(QLabel("单一操作列表"))
        host.show()
        self.app.processEvents()
        self.assertTrue(popover.show_for(anchor, prefer_above=True))

        global_position = popover.mapToGlobal(popover.rect().center())
        event = QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(popover.mapFromGlobal(global_position)),
            QPointF(global_position),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        popover.eventFilter(host.windowHandle(), event)

        self.assertTrue(popover.isVisible())
        host.deleteLater()

    def test_product_action_row_child_label_click_triggers_action(self):
        row = ProductActionRow("添加文件", "图片、文档或其他工作资料")
        hits = []
        row.clicked.connect(lambda: hits.append("file"))
        row.show()
        self.app.processEvents()

        QTest.mouseClick(row.title_label, Qt.LeftButton)
        self.app.processEvents()

        self.assertEqual(hits, ["file"])
        row.deleteLater()

    def test_model_selection_updates_session_meta_and_refreshes_controls(self):
        state = SimpleNamespace(selected_model_id="model-a", persisted_conversation_meta={})

        class WindowStub:
            _set_session_model_id = MainWindow._set_session_model_id

            def __init__(self):
                self.saved_session_ids = []
                self.model_refreshes = 0
                self.badge_refreshes = 0

            def get_current_session(self):
                return state

            def _model_profile_for_state(self, _state=None, model_id=None):
                if model_id == "model-b":
                    return {"id": "model-b", "display_name": "B"}
                return {}

            def save_chat_history(self, session_id=None):
                self.saved_session_ids.append(session_id)

            def refresh_model_selector(self):
                self.model_refreshes += 1

            def refresh_context_badges(self):
                self.badge_refreshes += 1

            def add_system_toast(self, *_args, **_kwargs):
                raise AssertionError("successful selection must not show an error toast")

        state.session_id = "session-1"
        window = WindowStub()

        self.assertTrue(MainWindow.on_model_selection_changed(window, "model-b"))
        self.assertEqual(state.selected_model_id, "model-b")
        self.assertEqual(state.persisted_conversation_meta["selected_model_id"], "model-b")
        self.assertEqual(window.saved_session_ids, ["session-1"])
        self.assertEqual(window.model_refreshes, 1)
        self.assertEqual(window.badge_refreshes, 1)

    def test_thinking_stays_expanded_when_finalized(self):
        bubble = ChatBubble("Agent", "")
        bubble.update_thinking("先检查环境")
        bubble.think_toggle_btn.setChecked(True)
        bubble.update_thinking(duration=2.5, is_final=True)
        self.assertTrue(bubble.think_toggle_btn.isChecked())
        self.assertIn("2.5 秒", bubble.think_toggle_btn.text())
        bubble.deleteLater()

    def test_agent_bubble_has_no_sub_agent_pip_or_raw_log_widgets(self):
        bubble = ChatBubble("Agent", "")
        self.assertFalse(hasattr(bubble, "sub_agent_indicators"))
        self.assertFalse(hasattr(bubble, "sub_agent_logs"))
        self.assertFalse(hasattr(bubble, "update_sub_agent_log"))
        bubble.deleteLater()

    def test_manual_skill_toggle_messages_are_hidden_but_ai_creation_is_visible(self):
        enabled = {
            "role": "assistant",
            "content": "◈ 已启用能力：visualize，可被 AI 发现",
            "meta": {
                "ui_only": True,
                "skill_change": {"source": "ui", "action": "enabled"},
            },
        }
        created = {
            "role": "assistant",
            "content": "◈ AI 已创建能力：demo，现已可用",
            "meta": {
                "ui_only": True,
                "skill_change": {"source": "ai", "action": "created"},
            },
        }
        self.assertTrue(is_hidden_manual_skill_change_message(enabled))
        self.assertFalse(is_hidden_manual_skill_change_message(created))

        state = SimpleNamespace(messages=[])

        class WindowStub:
            def get_session(self, _session_id):
                return state

            def add_chat_bubble(self, *_args, **_kwargs):
                raise AssertionError("manual Skill toggles must not create a chat bubble")

        applied = MainWindow._append_skill_change_conversation_event(
            WindowStub(),
            {
                "event_id": "toggle-1",
                "source": "ui",
                "action": "enabled",
                "skill_names": ["visualize"],
            },
            "session-1",
        )
        self.assertTrue(applied)
        self.assertEqual(state.messages, [])

    def test_deep_thinking_fold_hides_reasoning_and_tools_together(self):
        bubble = ChatBubble("Agent", "")
        bubble.update_thinking("先检查环境")
        tool = ToolCallCard("run_command", {"command": "pytest"}, "tool-1")
        bubble.add_tool_card(tool)
        self.assertFalse(bubble.think_toggle_btn.isChecked())
        self.assertTrue(bubble.think_container.isHidden())
        bubble.think_toggle_btn.setChecked(True)
        self.assertFalse(bubble.think_container.isHidden())
        self.assertIn(tool, bubble.timeline_events)
        bubble.think_toggle_btn.setChecked(False)
        self.assertTrue(bubble.think_container.isHidden())
        bubble.deleteLater()

    def test_empty_thinking_expansion_does_not_create_timeline_height(self):
        bubble = ChatBubble("Agent", "", thinking="...")
        bubble.think_toggle_btn.setChecked(True)
        self.app.processEvents()
        self.assertTrue(bubble.think_container.isHidden())

        bubble.update_thinking("正在检查")
        self.app.processEvents()
        self.assertFalse(bubble.think_container.isHidden())

        expanded_height = bubble.sizeHint().height()
        bubble.think_toggle_btn.setChecked(False)
        self.app.processEvents()
        self.assertTrue(bubble.think_container.isHidden())
        self.assertLess(bubble.sizeHint().height(), expanded_height)
        bubble.deleteLater()

    def test_turn_group_keeps_multiple_compact_thinking_stages_and_separators(self):
        group = AssistantTurnGroup("compact-turn")
        for index in range(5):
            bubble = ChatBubble("Agent", "")
            group.add_stage(bubble)
            bubble.update_thinking(f"阶段 {index + 1}", duration=index + 1, is_final=True)

        self.assertEqual(len(group.stage_bubbles), 5)
        self.assertEqual(len(group.stage_separators), 4)
        self.assertTrue(all(not bubble.isHidden() for bubble in group.stage_bubbles))
        self.assertTrue(all(not separator.isHidden() for separator in group.stage_separators))
        margins = group.stage_separators[0].layout().contentsMargins()
        self.assertEqual(margins.left(), DesignTokens.assistant_stage_separator_indent)
        self.assertEqual(margins.top(), DesignTokens.assistant_stage_separator_vertical_margin)
        self.assertEqual(margins.bottom(), DesignTokens.assistant_stage_separator_vertical_margin)
        group.deleteLater()

    def test_turn_group_hides_empty_stage_and_its_separator(self):
        group = AssistantTurnGroup("empty-stage-turn")
        first = ChatBubble("Agent", "", thinking="先分析")
        empty = ChatBubble("Agent", "")
        group.add_stage(first)
        group.add_stage(empty)

        self.assertFalse(first.isHidden())
        self.assertTrue(empty.isHidden())
        self.assertTrue(group.stage_separators[0].isHidden())

        empty.set_main_content("阶段回复", final=True)
        self.app.processEvents()
        self.assertFalse(empty.isHidden())
        self.assertFalse(group.stage_separators[0].isHidden())
        group.deleteLater()

    def test_stage_without_reply_does_not_reserve_text_editor_height(self):
        bubble = ChatBubble("Agent", "", thinking="只显示思考标题")
        self.assertTrue(bubble.content_rich_edit.isHidden())
        self.assertTrue(bubble.content_plain_edit.isHidden())

        bubble.set_main_content("阶段回复", final=True)
        self.app.processEvents()
        self.assertFalse(bubble.content_edit.isHidden())

        bubble.set_main_content("", final=True)
        self.app.processEvents()
        self.assertTrue(bubble.content_edit.isHidden())
        bubble.deleteLater()

    def test_stage_reply_remains_visible_when_thinking_segment_finishes(self):
        bubble = ChatBubble("Agent", "")
        bubble.set_main_content("阶段性文字", final=True)
        self.assertEqual(bubble.freeze_content_fragment(), "阶段性文字")
        self.assertEqual(bubble.main_content_text, "阶段性文字")
        self.assertIn("阶段性文字", bubble.content_edit.toPlainText())
        bubble.deleteLater()

    def test_guidance_status_updates_outside_thinking_segment(self):
        class TimelineStub:
            _timeline_find_event = MainWindow._timeline_find_event
            _timeline_set_guidance_status = MainWindow._timeline_set_guidance_status

        checkpoint = GuidanceTimelineEvent("guide-3", "先跑单测", status="waiting_tool")
        state = SimpleNamespace(
            ui_timeline_events=[{
                "kind": "guidance", "message_id": "guide-3", "status": "waiting_tool", "finished_at": None,
            }],
            guidance_widgets={"guide-3": checkpoint},
        )
        self.assertTrue(TimelineStub()._timeline_set_guidance_status(state, "guide-3", "applied"))
        self.assertEqual(checkpoint.status, "applied")
        checkpoint.deleteLater()

    def test_live_guidance_splits_thinking_into_conversational_segments(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_guidance")
            state.live_activity = True
            state.active_turn_id = 1
            first = window._append_live_thinking_segment(state)
            first_group = state.active_agent_turn_group
            first.update_thinking("先分析")
            first.set_main_content("阶段回复", final=False)
            state.current_content_buffer = "阶段回复"
            state.current_thinking_buffer = "先分析"

            message = {
                "id": "guide-live",
                "role": "user",
                "content": "调整方向",
                "meta": {"same_turn_guidance": True, "turn_id": "1"},
            }
            window._render_turn_guidance_checkpoint(state, message, "调整方向", [])
            guidance = state.guidance_widgets["guide-live"]
            continuation = state.temp_thinking_bubble
            guidance_wrapper = guidance.parentWidget()

            self.assertEqual(first.main_content_text, "阶段回复")
            self.assertIsNot(first, continuation)
            self.assertLess(state.chat_layout.indexOf(first_group), state.chat_layout.indexOf(guidance_wrapper))
            continuation_group = continuation.parentWidget()
            self.assertIsInstance(continuation_group, AssistantTurnGroup)
            self.assertLess(state.chat_layout.indexOf(guidance_wrapper), state.chat_layout.indexOf(continuation_group))
            self.assertIn("深度思考", first.think_toggle_btn.text())
            self.assertIn("深度思考", continuation.think_toggle_btn.text())
            self.assertEqual(continuation.session_id, state.session_id)
            self.assertIs(continuation.chat_storage, window.chat_storage)
            self.assertFalse(first.copy_result_btn.isVisible())
            self.assertFalse(first.office_draft_btn.isVisible())
        finally:
            window.close()
            window.deleteLater()

    def test_rejected_daemon_guidance_does_not_mark_reply_as_stopped(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            bubble = ChatBubble("Agent", "")
            bubble.set_main_content("仍在处理", final=False)
            state.temp_thinking_bubble = bubble
            state.last_agent_bubble = bubble
            state.active_turn_id = 1
            worker = object()
            state.guidance_workers = [worker]
            message = {"id": "guide-rejected", "role": "user", "content": "调整"}
            with patch.object(window, "_restore_rejected_guidance"):
                window._handle_daemon_guidance_result(
                    {"status": "error", "accepted": False},
                    worker,
                    state.session_id,
                    1,
                    message,
                    "调整",
                    [],
                    "调整",
                    [],
                )
            self.assertEqual(bubble.main_content_text, "仍在处理")
            self.assertNotIn("任务已停止", bubble.main_content_text)
        finally:
            window.close()
            window.deleteLater()

    def test_stop_marks_partial_stage_without_message_actions(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_stop_stage")
            state.active_turn_id = 1
            bubble = window._append_live_thinking_segment(state)
            state.current_content_buffer = "部分结果"
            bubble.set_main_content("部分结果", final=False)
            window.stop_agent()
            self.assertIn("任务已停止", bubble.main_content_text)
            self.assertTrue(bubble.copy_result_btn.isHidden())
            self.assertTrue(bubble.office_draft_btn.isHidden())
        finally:
            window.close()
            window.deleteLater()

    def test_error_finishes_current_group_without_message_actions_and_persists_status(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_error_stage")
            state.live_activity = True
            state.active_turn_id = 1
            bubble = window._append_live_thinking_segment(state)
            window.handle_llm_response({"error": "provider failed"}, state.session_id, turn_id=1)
            self.assertIn("provider failed", bubble.main_content_text)
            self.assertTrue(bubble.copy_result_btn.isHidden())
            self.assertTrue(bubble.office_draft_btn.isHidden())
            error_message = state.messages[-1]
            self.assertTrue(error_message["meta"]["ui_only"])
            self.assertEqual(error_message["meta"]["ui_reply_kind"], "error")
            self.assertFalse(any(message.get("id") == error_message["id"] for message in window._messages_for_worker(state, {})))
        finally:
            window.close()
            window.deleteLater()

    def test_tool_rounds_append_stages_inside_one_assistant_turn_group(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_unified_turn")
            state.live_activity = True
            state.active_turn_id = 1
            first = window._append_live_thinking_segment(state)
            first.update_thinking("先检查")
            state.current_content_buffer = "先执行检查。"
            window.flush_session_content(state.session_id, final=False)

            window.add_tool_card(
                {"id": "tool-unified", "name": "run_command", "args": {"command": "pytest"}},
                session_id=state.session_id,
                animate=False,
            )
            group = state.active_agent_turn_group
            self.assertIsInstance(group, AssistantTurnGroup)
            self.assertEqual(len(group.stage_bubbles), 1)
            self.assertFalse(first.copy_result_btn.isVisible())
            self.assertFalse(first.office_draft_btn.isVisible())

            window.handle_thinking_signal("继续分析", state.session_id, turn_id=1)
            second = state.temp_thinking_bubble
            self.assertIsNot(first, second)
            self.assertIs(second.parentWidget(), group)
            self.assertEqual(len(group.stage_bubbles), 2)
            self.assertEqual(state.chat_layout.indexOf(group), 0)
            window.handle_content_signal("检查已通过。", state.session_id, turn_id=1)
            window.handle_llm_response(
                {
                    "role": "assistant",
                    "content": "检查已通过。",
                    "reasoning": "继续分析",
                    "duration": 0.2,
                    "generated_messages": [
                        {
                            "id": "assistant-live-stage",
                            "role": "assistant",
                            "content": "先执行检查。",
                            "reasoning_content": "先检查",
                            "tool_calls": [{
                                "id": "tool-unified",
                                "type": "function",
                                "function": {"name": "run_command", "arguments": "{\"command\": \"pytest\"}"},
                            }],
                        },
                        {"id": "tool-live-result", "role": "tool", "tool_call_id": "tool-unified", "content": "ok"},
                        {
                            "id": "assistant-live-final",
                            "role": "assistant",
                            "content": "检查已通过。",
                            "reasoning_content": "继续分析",
                        },
                    ],
                },
                state.session_id,
                turn_id=1,
            )
            self.assertIs(state.chat_layout.itemAt(0).widget(), group)
            self.assertTrue(first.copy_result_btn.isHidden())
            self.assertFalse(second.copy_result_btn.isHidden())
            assistant_messages = [message for message in state.messages if message.get("role") == "assistant"]
            self.assertEqual(assistant_messages[0]["meta"]["ui_reply_kind"], "stage")
            self.assertEqual(assistant_messages[-1]["meta"]["ui_reply_kind"], "final")
        finally:
            window.close()
            window.deleteLater()

    def test_history_restores_one_group_and_final_actions_only(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_unified_history")
            messages = [
                {
                    "id": "assistant-stage",
                    "role": "assistant",
                    "content": "先执行检查。",
                    "reasoning_content": "分析环境",
                    "tool_calls": [{
                        "id": "tool-history",
                        "type": "function",
                        "function": {"name": "run_command", "arguments": "{\"command\": \"pytest\"}"},
                    }],
                },
                {"id": "tool-result", "role": "tool", "tool_call_id": "tool-history", "content": "ok"},
                {
                    "id": "assistant-final",
                    "role": "assistant",
                    "content": "检查已通过。",
                    "reasoning_content": "汇总结论",
                },
            ]
            inserted = window.render_message_batch(messages, state.session_id, animate=False)
            self.assertEqual(inserted, 1)
            group = state.chat_layout.itemAt(0).widget()
            self.assertIsInstance(group, AssistantTurnGroup)
            self.assertEqual(len(group.stage_bubbles), 2)
            stage, final = group.stage_bubbles
            self.assertTrue(stage.copy_result_btn.isHidden())
            self.assertTrue(stage.office_draft_btn.isHidden())
            self.assertFalse(final.copy_result_btn.isHidden())
            self.assertFalse(final.office_draft_btn.isHidden())
            self.assertEqual(final.main_content_text, "检查已通过。")
        finally:
            window.close()
            window.deleteLater()

    def test_history_guidance_splits_groups_and_suppresses_pre_guidance_actions(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_legacy_guidance_history")
            messages = [
                {"id": "assistant-before-guide", "role": "assistant", "content": "先给出阶段结论。"},
                {
                    "id": "legacy-guide",
                    "role": "user",
                    "content": "继续验证测试",
                    "meta": {"same_turn_guidance": True, "turn_id": "1"},
                },
                {"id": "assistant-after-guide", "role": "assistant", "content": "最终结论。"},
            ]
            inserted = window.render_message_batch(messages, state.session_id, animate=False)
            self.assertEqual(inserted, 3)
            first_group = state.chat_layout.itemAt(0).widget()
            guidance = state.chat_layout.itemAt(1).widget()
            second_group = state.chat_layout.itemAt(2).widget()
            self.assertIsInstance(first_group, AssistantTurnGroup)
            self.assertIsInstance(second_group, AssistantTurnGroup)
            self.assertIsNotNone(guidance)
            self.assertTrue(first_group.stage_bubbles[-1].copy_result_btn.isHidden())
            self.assertFalse(second_group.stage_bubbles[-1].copy_result_btn.isHidden())
        finally:
            window.close()
            window.deleteLater()

    def test_worker_messages_strip_unified_ui_metadata(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            state.messages = [{
                "id": "assistant-final",
                "role": "assistant",
                "content": "完成",
                "meta": {
                    "ui_turn_id": "1",
                    "ui_turn_group_id": "turn-1-group-1",
                    "ui_stage_id": "turn-1-group-1:stage-1",
                    "ui_reply_kind": "final",
                    "provider_hint": "keep",
                },
            }]
            messages = window._messages_for_worker(state, {})
            self.assertEqual(messages[0]["meta"], {"provider_hint": "keep"})
            self.assertEqual(state.messages[0]["meta"]["ui_turn_group_id"], "turn-1-group-1")
        finally:
            window.close()
            window.deleteLater()

    def test_history_missing_final_body_shows_error_without_actions(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_missing_history_final")
            messages = [
                {
                    "id": "assistant-stage",
                    "role": "assistant",
                    "content": "阶段内容不能成为最终答复。",
                    "tool_calls": [{
                        "id": "tool-missing-final",
                        "type": "function",
                        "function": {"name": "run_command", "arguments": "{}"},
                    }],
                },
                {"role": "tool", "tool_call_id": "tool-missing-final", "content": "ok"},
                {"id": "assistant-empty-final", "role": "assistant", "content": ""},
            ]
            self.assertEqual(window.render_message_batch(messages, state.session_id, animate=False), 1)
            group = state.chat_layout.itemAt(0).widget()
            final = group.stage_bubbles[-1]
            self.assertIn("未收到最终答复", final.main_content_text)
            self.assertNotIn("阶段内容不能成为最终答复", final.main_content_text)
            self.assertTrue(final.copy_result_btn.isHidden())
            self.assertTrue(final.office_draft_btn.isHidden())
        finally:
            window.close()
            window.deleteLater()

    def test_live_missing_final_body_does_not_promote_stage_reply(self):
        window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="test_missing_live_final")
            state.live_activity = True
            state.active_turn_id = 1
            bubble = window._append_live_thinking_segment(state)
            window.handle_llm_response(
                {
                    "role": "assistant",
                    "content": "",
                    "generated_messages": [
                        {
                            "id": "assistant-stage",
                            "role": "assistant",
                            "content": "阶段内容不能成为最终答复。",
                            "tool_calls": [{
                                "id": "tool-live-missing-final",
                                "type": "function",
                                "function": {"name": "run_command", "arguments": "{}"},
                            }],
                        },
                        {"role": "tool", "tool_call_id": "tool-live-missing-final", "content": "ok"},
                        {"id": "assistant-empty-final", "role": "assistant", "content": ""},
                    ],
                },
                state.session_id,
                turn_id=1,
            )
            self.assertIn("未收到最终答复", bubble.main_content_text)
            self.assertNotIn("阶段内容不能成为最终答复", bubble.main_content_text)
            self.assertTrue(bubble.copy_result_btn.isHidden())
            self.assertTrue(bubble.office_draft_btn.isHidden())
            assistants = [message for message in state.messages if message.get("role") == "assistant"]
            self.assertEqual(assistants[-1]["meta"]["ui_reply_kind"], "error")
        finally:
            window.close()
            window.deleteLater()

    def test_live_continuation_renders_registered_inline_visualization(self):
        with tempfile.TemporaryDirectory() as root:
            window = MainWindow()
            try:
                state = window.get_current_session()
                window._retire_session_empty_state(state, reason="test_inline_visualization")
                window.chat_storage = ChatStorage(os.path.join(root, "chat.sqlite"))
                path = os.path.join(root, "live-visual.html")
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write('<div id="live-visual">ok</div>')
                with open(path, "rb") as handle:
                    digest = hashlib.sha256(handle.read()).hexdigest()
                artifact = {
                    "file": "live-visual-12345678.html",
                    "path": path,
                    "sha256": digest,
                    "title": "续接视图",
                    "origins": [],
                }
                window.chat_storage.register_inline_visualization(state.session_id, artifact)

                with patch.object(window.config_manager, "is_skill_enabled", return_value=True):
                    continuation = window._append_live_thinking_segment(state)
                directive = '::cowork-inline-vis{file="live-visual-12345678.html"}'
                with patch("main.QTimer.singleShot"):
                    continuation.set_main_content(f"结果\n\n{directive}", final=True)

                self.assertEqual(continuation.session_id, state.session_id)
                self.assertIs(continuation.chat_storage, window.chat_storage)
                self.assertTrue(continuation.visualize_enabled)
                self.assertEqual(len(continuation.inline_visualization_cards), 1)
                self.assertNotIn("cowork-inline-vis", continuation.content_edit.toPlainText())
            finally:
                window.close()
                window.deleteLater()

    def test_ui_timeline_metadata_validation_is_explicit(self):
        events, warning = MainWindow._normalize_ui_timeline_events(
            None,
            [{"kind": "thinking", "sequence": 1, "started_at": 1.0, "text": "检查"}],
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(warning, "")
        events, warning = MainWindow._normalize_ui_timeline_events(None, [{"kind": "unknown"}])
        self.assertEqual(events, [])
        self.assertTrue(warning)
        events, warning = MainWindow._normalize_ui_timeline_events(
            None,
            [{
                "kind": "thinking",
                "sequence": 2,
                "started_at": 2.0,
                "group_id": "turn-1-group-1",
                "stage_id": "turn-1-group-1:stage-1",
            }],
        )
        self.assertEqual(events[0]["group_id"], "turn-1-group-1")
        self.assertEqual(warning, "")
        events, warning = MainWindow._normalize_ui_timeline_events(
            None,
            [{"kind": "thinking", "sequence": 3, "started_at": 3.0, "group_id": "incomplete"}],
        )
        self.assertEqual(events, [])
        self.assertIn("不完整", warning)

    def test_ui_timeline_coalesces_deltas_and_splits_at_guidance(self):
        class TimelineStub:
            _timeline_append_event = MainWindow._timeline_append_event
            _timeline_find_event = MainWindow._timeline_find_event
            _timeline_append_text_delta = MainWindow._timeline_append_text_delta

        window = TimelineStub()
        state = SimpleNamespace(active_turn_id=3, ui_timeline_events=[], ui_timeline_sequence=0)
        first = window._timeline_append_text_delta(state, "thinking", "先检查")
        second = window._timeline_append_text_delta(state, "thinking", "环境")
        self.assertIs(first, second)
        self.assertEqual(first["text"], "先检查环境")
        window._timeline_append_event(
            state,
            "guidance",
            status="queued",
            message_id="guide-4",
            text="优先测试",
        )
        continued = window._timeline_append_text_delta(state, "thinking", "继续")
        self.assertIsNot(first, continued)
        self.assertEqual([event["kind"] for event in state.ui_timeline_events], ["thinking", "guidance", "thinking"])

    def test_persisted_timeline_rebuilds_send_time_order(self):
        state = SimpleNamespace(
            session_id="session-timeline",
            live_activity=False,
            last_agent_bubble=None,
            tool_cards={},
            ui_timeline_events=[
                {"sequence": 1, "turn_id": "7", "kind": "thinking", "status": "completed", "started_at": 1.0, "finished_at": 2.0, "text": "先分析"},
                {"sequence": 2, "turn_id": "7", "kind": "tool", "status": "completed", "started_at": 2.0, "finished_at": 3.0, "tool_call_id": "tool-7", "text": "运行测试"},
                {"sequence": 3, "turn_id": "7", "kind": "guidance", "status": "applied", "started_at": 2.5, "finished_at": 3.1, "message_id": "guide-7", "text": "优先测试"},
                {"sequence": 4, "turn_id": "7", "kind": "thinking", "status": "completed", "started_at": 3.1, "finished_at": 4.0, "text": "继续分析"},
                {"sequence": 5, "turn_id": "7", "kind": "final_content", "status": "completed", "started_at": 4.0, "finished_at": 4.2, "text": "最终结果"},
            ],
        )

        class RenderStub:
            _render_persisted_timeline_items = MainWindow._render_persisted_timeline_items

            def __init__(self):
                self.bubbles = []
                self.render_order = []

            def add_chat_bubble(self, *_args, **_kwargs):
                bubble = ChatBubble("Agent", "")
                self.bubbles.append(bubble)
                self.render_order.append(("thinking", bubble))
                return bubble

            def add_turn_guidance_inline(self, message, **kwargs):
                guidance = GuidanceTimelineEvent(
                    message.get("id") or "",
                    kwargs.get("display_content") or "",
                    status=kwargs.get("status") or "applied",
                )
                self.render_order.append(("guidance", guidance))
                return guidance

            def add_tool_card(self, data, **_kwargs):
                card = ToolCallCard(data["name"], data["args"], data["id"], meta=data.get("meta"))
                state.tool_cards[data["id"]] = card
                state.last_agent_bubble.add_tool_card(card)

            def update_tool_card(self, data, **_kwargs):
                state.tool_cards[data["id"]].set_result(data.get("result") or "", data.get("result_obj"))

            def _assistant_source_message_id_from_messages(self, messages):
                return str((messages or [{}])[-1].get("id") or "")

            def _message_display_content(self, message):
                return str((message or {}).get("content") or "")

            def _message_user_attachments(self, _message):
                return []

        render_items = [
            {
                "type": "assistant",
                "content": "阶段回复",
                "messages": [{"id": "assistant-1"}],
                "tool_calls": [{"id": "tool-7", "name": "run_command", "args": {"command": "tests"}, "result": "ok"}],
            },
            {"type": "guidance", "message": {"id": "guide-7", "content": "优先测试", "meta": {"same_turn_guidance": True, "turn_id": "7"}}},
            {"type": "assistant", "content": "最终结果", "messages": [{"id": "assistant-2"}], "tool_calls": []},
        ]
        window = RenderStub()
        self.assertTrue(window._render_persisted_timeline_items(render_items, state))
        self.assertEqual([kind for kind, _widget in window.render_order], ["thinking", "guidance", "thinking"])
        self.assertTrue(any(isinstance(item, ToolCallCard) for item in window.bubbles[0].timeline_events))
        self.assertEqual(window.bubbles[1].main_content_text, "最终结果")
        for _kind, widget in window.render_order:
            widget.deleteLater()

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
