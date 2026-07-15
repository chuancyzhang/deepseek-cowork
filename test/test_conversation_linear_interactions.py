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
            first = ChatBubble("Agent", "", thinking="...")
            first.update_thinking("先分析")
            first.set_main_content("阶段回复", final=False)
            state.chat_layout.insertWidget(state.chat_layout.count() - 1, first)
            state.temp_thinking_bubble = first
            state.last_agent_bubble = first
            state.live_activity = True
            state.active_turn_id = 1
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
            self.assertLess(state.chat_layout.indexOf(first), state.chat_layout.indexOf(guidance_wrapper))
            self.assertLess(state.chat_layout.indexOf(guidance_wrapper), state.chat_layout.indexOf(continuation))
            self.assertIn("深度思考", first.think_toggle_btn.text())
            self.assertIn("深度思考", continuation.think_toggle_btn.text())
            self.assertEqual(continuation.session_id, state.session_id)
            self.assertIs(continuation.chat_storage, window.chat_storage)
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
