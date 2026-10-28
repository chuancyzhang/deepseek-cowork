"""Questionnaire defaults, explicit submission, receipts and history ownership."""
import copy
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from main import AssistantTurnGroup, ChatBubble, InlineInteractionCard, MainWindow, ToolCallCard
from ui.interaction_cards import questionnaire_result
from test_interaction_lifecycle import InteractionHost
from core.config_manager import ConfigManager
from ui_window_cleanup import close_test_window


def request():
    return {"request_id": "questions", "kind": "questionnaire", "title": "明确分析需求",
            "questions": [
                {"id": "scope", "header": "分析范围", "question": "优先分析什么？", "options": [
                    {"label": "整体情况", "value": "overall", "description": "先建立整体认识，再选择深入方向。"},
                    {"label": "具体问题", "value": "specific"},
                    {"label": "自定义", "value": "__custom__"}]},
                {"id": "format", "header": "呈现方式", "question": "怎样呈现？", "options": [
                    {"label": "先给结论", "value": "summary"}, {"label": "完整报告", "value": "report"}]}]}


def result(answers, identity="questions", status="completed"):
    return {"source_tool": "request_user_input", "interaction_request": request(),
            "interaction_response": {"request_id": identity, "status": status, "approved": True,
                                     "answers": copy.deepcopy(answers)}}


class _ShowTracker(QObject):
    def __init__(self):
        super().__init__()
        self.unexpected = []

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
            self.unexpected.append(type(obj).__name__)
        return False


class QuestionnaireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.host = InteractionHost()
        self.host.state.tool_cards = {}
        self.host.resize(640, 760)
        self.host.show()
        self.app.processEvents()
        for name in ("log_ui_navigation", "log_sub_agent_runtime"):
            self.enterContext(patch("main." + name))

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def show_card(self, resolver=None, payload=None):
        return self.host._show_inline_interaction_request(payload or request(), "session", resolver or Mock(return_value=True))

    def test_defaults_are_selected_without_submitting_and_all_questions_submit_once(self):
        resolver = Mock(return_value=True)
        card = self.show_card(resolver)
        self.app.processEvents()
        self.assertEqual([c["group"].checkedButton().property("option_value") for c in card.question_controls], ["overall", "summary"])
        resolver.assert_not_called()
        self.assertEqual(sum(not s.body.isHidden() for s in card.questionnaire.sections), 1)
        QTest.mouseClick(card.submit_btn, Qt.LeftButton)
        resolver.assert_called_once()
        self.assertEqual(set(resolver.call_args.args[0]), {"scope", "format"})
        self.assertFalse(self.host.state.pending_interactions)
        self.assertEqual(card.receipt.label.text(), "已提交 2 项回答")
        self.assertTrue(card.questionnaire.isHidden())
        self.assertFalse(card.receipt.isHidden())
        self.assertTrue(card.receipt.details.isHidden())
        QTest.mouseClick(card.receipt.view_btn, Qt.LeftButton)
        self.assertFalse(card.receipt.details.isHidden())
        self.assertEqual(card.receipt.rows[0].answer_label.text(), "整体情况")
        card._submit()
        resolver.assert_called_once()

    def test_switching_questions_preserves_custom_text_and_clears_only_its_default(self):
        card = self.show_card()
        first, second = card.questionnaire.sections
        first.input.setText("只分析这部分")
        self.assertIsNone(first.group.checkedButton())
        self.assertIsNotNone(second.group.checkedButton())
        second.header.click()
        self.assertTrue(first.body.isHidden())
        self.assertIn("只分析这部分", first.summary_label.text())
        first.header.click()
        self.assertEqual(first.input.text(), "只分析这部分")
        first.buttons[1].click()
        self.assertEqual(first.input.text(), "")
        self.assertEqual(first.group.checkedButton().property("option_value"), "specific")

    def test_empty_custom_answer_is_not_silently_replaced_by_default(self):
        resolver = Mock(return_value=True)
        card = self.show_card(resolver)
        section = card.questionnaire.sections[1]
        section.input.setText("custom")
        section.input.clear()
        card._submit()
        resolver.assert_not_called()
        self.assertTrue(section.header.isChecked())
        self.assertIsNone(section.group.checkedButton())
        self.assertFalse(card.validation_label.isHidden())

    def test_keyboard_can_expand_change_and_submit_without_mouse(self):
        resolver = Mock(return_value=True)
        card = self.show_card(resolver)
        self.app.processEvents()
        section = card.questionnaire.sections[1]
        section.header.setFocus()
        QTest.keyClick(section.header, Qt.Key_Space)
        self.assertTrue(section.header.isChecked())
        section.buttons[1].setFocus()
        QTest.keyClick(section.buttons[1], Qt.Key_Space)
        self.assertEqual(section.group.checkedButton().property("option_value"), "report")
        card.submit_btn.setFocus()
        QTest.keyClick(card.submit_btn, Qt.Key_Space)
        resolver.assert_called_once()
        self.assertEqual(card.receipt.rows[1].answer_label.text(), "完整报告")

    def test_submission_failure_keeps_all_answers_and_retry_uses_same_request(self):
        resolver = Mock(side_effect=[ConnectionError("offline"), True])
        card = self.show_card(resolver)
        card.question_controls[1]["input"].setText("保留这段回答")
        card._submit()
        self.assertFalse(card.questionnaire.isHidden())
        self.assertTrue(card.receipt.isHidden())
        self.assertEqual(card.question_controls[1]["input"].text(), "保留这段回答")
        self.assertTrue(card.submit_btn.isEnabled())
        self.assertIn("提交失败", card.validation_label.text())
        card._submit()
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(resolver.call_args_list[0], resolver.call_args_list[1])
        self.assertEqual(card.receipt.rows[1].answer_label.text(), "保留这段回答")

    def test_terminal_event_before_ack_does_not_drop_confirmed_answers(self):
        def resolve(_answers):
            self.host.handle_interaction_finished({"session_id": "session", "run_id": "run",
                "request_id": "questions", "status": "completed", "answered": True})
            return True
        card = self.show_card(resolve)
        card._submit()
        self.assertEqual(card.receipt.label.text(), "已提交 2 项回答")
        self.assertEqual(len(card.receipt.rows), 2)

    def test_other_client_completion_waits_for_real_result_instead_of_using_ui_defaults(self):
        card = self.show_card()
        self.host.handle_interaction_finished({"session_id": "session", "run_id": "run",
            "request_id": "questions", "status": "completed", "answered": True})
        self.assertEqual(card.receipt.rows, [])
        self.assertEqual(card.receipt.label.text(), "回答已接收")
        self.host._restore_questionnaire_receipt(self.host.state, "call", result({"scope": {"text": "其他客户端的回答"}}))
        self.assertEqual(card.receipt.label.text(), "已提交 1 项回答")
        self.assertEqual(card.receipt.rows[0].answer_label.text(), "其他客户端的回答")
        self.assertEqual(len(self.host.findChildren(InlineInteractionCard)), 1)

    def test_cancel_keeps_copyable_draft_without_a_success_receipt(self):
        card = self.show_card()
        card.question_controls[0]["input"].setText("取消后保留")
        card.cancel_btn.click()
        self.assertIn("回答未提交", card.receipt.label.text())
        self.assertEqual(card.receipt.rows[0].answer_label.text(), "取消后保留")
        self.assertTrue(card.receipt.rows[0].answer_label.textInteractionFlags() & Qt.TextSelectableByMouse)
        self.assertFalse(self.host.state.pending_interactions)
        replay = self.show_card()
        self.assertIs(card, replay)
        self.assertFalse(self.host.state.pending_interactions)

    def test_execution_checkpoint_has_no_implicit_recommended_permission(self):
        payload = request()
        payload["metadata"] = {"purpose": "grill_checkpoint"}
        resolver = Mock(return_value=True)
        card = self.show_card(resolver, payload)
        self.assertTrue(all(c["group"].checkedButton() is None for c in card.question_controls))
        self.assertFalse(any(b.recommended for c in card.question_controls for b in c["buttons"]))
        card._submit()
        resolver.assert_not_called()

    def test_replayed_result_is_read_only_unique_and_outside_collapsed_process(self):
        group = AssistantTurnGroup("turn", self.host)
        self.host.state.chat_layout.insertWidget(0, group)
        stage = ChatBubble("Agent", "分析前需要你选择", parent=group)
        group.add_stage(stage)
        tool = ToolCallCard("request_user_input", {}, "call", parent=stage)
        stage.add_tool_card(tool)
        self.host.state.tool_cards["call"] = tool
        answers = {"scope": {"selected_options": ["specific"]}, "format": {"text": "自定义格式"}}
        payload = result(answers)
        original = copy.deepcopy(payload)
        self.host._restore_questionnaire_receipt(self.host.state, "call", json.dumps(payload))
        self.host._restore_questionnaire_receipt(self.host.state, "call", payload)
        card = self.host.state.interaction_cards["questions"]
        self.assertEqual(len(group.interaction_cards), 1)
        self.assertIs(card.parentWidget(), group)
        self.assertEqual(group.group_layout.indexOf(card), group.group_layout.indexOf(stage) + 1)
        group.finalize_process_only()
        group.set_process_expanded(False)
        self.app.processEvents()
        self.assertTrue(card.isVisible())
        self.assertEqual(card.receipt.rows[0].answer_label.text(), "具体问题")
        self.assertEqual(payload, original)
        self.assertEqual(self.host.state.messages, [])
        self.assertFalse(self.host.state.pending_interactions)

    def test_legacy_automatic_result_never_claims_user_submitted(self):
        self.host._restore_questionnaire_receipt(self.host.state, "call", result({"scope": {"text": "旧默认值"}}, status="auto_selected"))
        card = self.host.state.interaction_cards["questions"]
        self.assertIn("系统自动选择", card.receipt.label.text())
        self.assertNotIn("已提交", card.receipt.label.text())
        self.assertIsNone(questionnaire_result({"interaction_request": request(), "interaction_response": {"status": "running"}}))

    def test_construction_and_theme_refresh_never_show_unowned_windows(self):
        tracker = _ShowTracker()
        self.app.installEventFilter(tracker)
        try:
            card = self.show_card()
            card.refresh_theme()
            card.questionnaire.sections[1].header.click()
            card._submit()
            card.receipt.view_btn.click()
            self.app.processEvents()
            self.assertEqual(tracker.unexpected, [])
        finally:
            self.app.removeEventFilter(tracker)

    def test_narrow_layout_keeps_actions_visible_and_long_answers_copyable(self):
        payload = request()
        payload["questions"][0]["options"][0]["description"] = "一段需要换行的选项说明。" * 16
        card = self.show_card(payload=payload)
        self.host.setFixedWidth(380)
        QTest.qWait(30)
        self.host.adjustSize()
        QTest.qWait(30)
        self.assertLessEqual(card.questionnaire.scroll.height(), 420)
        for button in (card.submit_btn, card.cancel_btn):
            position = button.mapTo(card, button.rect().bottomRight())
            self.assertLess(position.x(), card.width())
            self.assertLess(position.y(), card.height())
        long_text = "需要完整保留的回答。" * 50
        card.question_controls[0]["input"].setText(long_text)
        card._submit()
        self.assertEqual(card.receipt.rows[0].answer_label.text(), long_text)


class QuestionnaireHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_saved_receipt_survives_initial_history_details_switch_and_reload(self):
        with tempfile.TemporaryDirectory(prefix="cowork-questionnaire-history-") as folder:
            with patch.dict(os.environ, {"APPDATA": folder, "LOCALAPPDATA": folder}), \
                    patch("core.config_manager.get_app_data_dir", return_value=folder), \
                    patch("core.config_manager.get_base_dir", return_value=folder):
                Path(folder, "config.json").write_text("{}", encoding="utf-8")
                window = MainWindow(config_manager=ConfigManager())
                window._startup_hydration_scheduled = True
                window._background_services_scheduled = True
                try:
                    window.show()
                    state = window.get_current_session()
                    payload = result({"scope": {"selected_options": ["specific"]},
                                      "format": {"text": "保留的自定义回答"}})
                    messages = [
                        {"id": "question-call", "role": "assistant", "content": "请确认分析范围",
                         "tool_calls": [{"id": "call", "type": "function", "function": {
                             "name": "request_user_input", "arguments": json.dumps(request())}}]},
                        {"id": "question-result", "role": "tool", "tool_call_id": "call",
                         "content": json.dumps(payload, ensure_ascii=False)},
                        {"id": "answer", "role": "assistant", "content": "按你的选择继续分析。"}]
                    window.chat_storage.save_conversation(state.session_id, messages, title="问卷回执", status="completed")
                    stored = window.chat_storage.get_messages(state.session_id)
                    other = window.create_new_session(make_current=False)
                    for _ in range(2):
                        window._reset_session_history_state(state)
                        state.messages = copy.deepcopy(stored)
                        state.history_loaded = True
                        state.session_status = "completed"
                        window._render_session_history_spans(state, [{"start": 0, "end": len(stored)}])
                        self.app.processEvents()
                        summary = state.history_detail_summaries[0]
                        card = state.interaction_cards["questions"]
                        self.assertFalse(summary.detail_button.isChecked())
                        self.assertTrue(card.isVisibleTo(summary))
                        self.assertIs(card.parentWidget(), summary.body)
                        self.assertEqual(card.receipt.rows[1].answer_label.text(), "保留的自定义回答")
                        summary.detail_button.setChecked(True)
                        deadline = time.monotonic() + 5
                        while summary.detail_loading and time.monotonic() < deadline:
                            self.app.processEvents()
                            time.sleep(.01)
                        self.assertFalse(summary.detail_loading)
                        self.assertFalse(summary.detail_error)
                        summary.detail_button.setChecked(False)
                        self.assertTrue(card.isVisibleTo(summary))
                        self.assertEqual(len(summary.findChildren(InlineInteractionCard)), 1)
                        for sid in (other, state.session_id):
                            window.set_current_session(sid)
                            self.app.processEvents()
                        self.assertIs(state.interaction_cards["questions"], card)
                        self.assertFalse(window.get_session(other).interaction_cards)
                        self.assertEqual(state.messages, stored)
                        self.assertFalse(state.pending_interactions)
                finally:
                    close_test_window(window)


if __name__ == "__main__":
    unittest.main()
