import os
import inspect
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QVBoxLayout, QWidget

from main import (
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
    TimelineTextEvent,
    ToolCallCard,
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

    def test_task_timeline_keeps_guidance_visible_and_starts_new_thinking_event(self):
        bubble = ChatBubble("Agent", "")
        bubble.update_thinking("先检查环境")
        first_thinking = bubble.current_thinking_event
        checkpoint = bubble.add_guidance_checkpoint("guide-1", "优先检查测试", status="queued")
        self.assertIsInstance(first_thinking, TimelineTextEvent)
        self.assertIsNotNone(first_thinking.finished_at)
        self.assertIsInstance(checkpoint, GuidanceTimelineEvent)
        self.assertTrue(checkpoint.isVisibleTo(bubble) or not bubble.isVisible())
        bubble.update_thinking("继续检查")
        self.assertIsNot(first_thinking, bubble.current_thinking_event)
        self.assertEqual(bubble.current_thinking_event.text(), "继续检查")
        bubble.update_guidance_checkpoint("guide-1", "applied")
        self.assertEqual(checkpoint.status, "applied")
        bubble.deleteLater()

    def test_task_timeline_freezes_streamed_content_before_guidance(self):
        bubble = ChatBubble("Agent", "")
        bubble.set_main_content("阶段性文字", final=True)
        checkpoint = bubble.add_guidance_checkpoint("guide-2", "调整方向", status="waiting_tool")
        fragments = [item for item in bubble.timeline_events if isinstance(item, TimelineTextEvent)]
        self.assertEqual(len(fragments), 1)
        self.assertEqual(fragments[0].kind, "content_fragment")
        self.assertEqual(fragments[0].text(), "阶段性文字")
        self.assertEqual(bubble.main_content_text, "")
        self.assertEqual(checkpoint.status, "waiting_tool")
        bubble.deleteLater()

    def test_task_timeline_keeps_running_tool_before_guidance_checkpoint(self):
        bubble = ChatBubble("Agent", "")
        bubble.update_thinking("准备执行")
        tool = ToolCallCard("run_command", {"command": "pytest"}, "tool-1")
        bubble.add_tool_card(tool)
        checkpoint = bubble.add_guidance_checkpoint("guide-3", "先跑单测", status="waiting_tool")
        self.assertLess(bubble.timeline_events.index(tool), bubble.timeline_events.index(checkpoint))
        tool.set_result("ok")
        self.assertTrue(tool.is_finished)
        self.assertEqual(checkpoint.status, "waiting_tool")
        bubble.deleteLater()

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

            def add_chat_bubble(self, *_args, **_kwargs):
                self.bubble = ChatBubble("Agent", "")
                return self.bubble

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
        self.assertEqual(
            [type(item) for item in window.bubble.timeline_events],
            [TimelineTextEvent, ToolCallCard, GuidanceTimelineEvent, TimelineTextEvent],
        )
        self.assertEqual(window.bubble.main_content_text, "最终结果")
        window.bubble.deleteLater()

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
