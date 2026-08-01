import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from core.conversation_render import build_conversation_render_spans
from main import (
    AssistantTurnGroup,
    HistoricalAssistantSummary,
    MainWindow,
    OfficeDraftTaskCard,
    SessionHistoryLoadWorker,
)


class HistoryPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()
        self.state = self.window.get_current_session()
        self.window.clear_chat_layout(self.state.chat_layout)
        self.state.empty_state = None
        self.state.history_loaded = True

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_history_assistant_turn_materializes_details_only_after_expand(self):
        messages = []
        group_id = "history-group"
        for index in range(4):
            messages.extend(
                [
                    {
                        "id": f"a{index}",
                        "role": "assistant",
                        "content": f"阶段 {index}",
                        "reasoning_content": f"思考 {index}",
                        "tool_calls": [
                            {
                                "id": f"tool-{index}",
                                "function": {"name": "demo", "arguments": "{}"},
                            }
                        ],
                        "meta": {
                            "ui_turn_group_id": group_id,
                            "ui_stage_id": f"{group_id}:{index}",
                            "ui_reply_kind": "stage",
                        },
                    },
                    {
                        "id": f"result-{index}",
                        "role": "tool",
                        "tool_call_id": f"tool-{index}",
                        "content": "完成",
                    },
                ]
            )
        messages.append(
            {
                "id": "final",
                "role": "assistant",
                "content": "最终答复",
                "meta": {
                    "ui_turn_group_id": group_id,
                    "ui_stage_id": f"{group_id}:final",
                    "ui_reply_kind": "final",
                },
            }
        )
        self.state.messages = messages
        self.state.render_items = [{"start": 0, "end": len(messages)}]

        inserted = self.window._render_session_history_spans(self.state, self.state.render_items)

        self.assertEqual(inserted, 1)
        summary = self.state.chat_layout.itemAt(0).widget()
        self.assertIsInstance(summary, HistoricalAssistantSummary)
        self.assertIn("assistant:history-group", self.state.render_nodes)
        self.assertFalse(self.state.render_nodes["assistant:history-group"]["details_materialized"])
        self.assertEqual(summary.detail_batch_index, 0)
        self.assertFalse(self.state.chat_layout.itemAt(0).widget().findChildren(AssistantTurnGroup))
        self.assertEqual(self.state.tool_cards, {})

        summary.detail_button.setChecked(True)
        for _ in range(20):
            self.app.processEvents()
            if summary.detail_batch_index == len(summary.detail_batches):
                break

        self.assertEqual(summary.detail_batch_index, len(summary.detail_batches))
        self.assertEqual(len(self.state.tool_cards), 4)
        self.assertTrue(self.state.render_nodes["assistant:history-group"]["details_materialized"])
        group_count = len(summary.detail_groups)
        summary.detail_button.setChecked(False)
        summary.detail_button.setChecked(True)
        self.app.processEvents()
        self.assertEqual(len(summary.detail_groups), group_count)

    def test_history_worker_returns_pure_data_and_render_spans(self):
        class Storage:
            def get_conversation_meta(self, _session_id):
                return {"run_phase": "Idle"}

            def get_conversation_record(self, _session_id):
                return {"status": "draft"}

            def get_messages(self, _session_id):
                return [
                    {"id": "u1", "role": "user", "content": "问题"},
                    {"id": "a1", "role": "assistant", "content": "回答"},
                ]

            def list_agents(self, _session_id):
                return []

        results = []
        worker = SessionHistoryLoadWorker(Storage(), "session", 7)
        worker.finished_signal.connect(results.append)
        worker.run()

        self.assertEqual(results[0]["token"], 7)
        self.assertEqual(results[0]["spans"], [{"start": 0, "end": 1}, {"start": 1, "end": 2}])
        self.assertNotIn("widget", results[0])

    def test_history_load_rebuilds_spans_after_message_normalization(self):
        raw_messages = [
            {"id": "u1", "role": "user", "content": "问题"},
            {
                "id": "skill-context",
                "role": "system",
                "content": "runtime only",
                "meta": {
                    "hidden": True,
                    "kind": "skill_context",
                    "source": "skill_prompt_tool_search",
                },
            },
            {"id": "a1", "role": "assistant", "content": "回答"},
        ]
        normalized_messages = [raw_messages[0], raw_messages[2]]
        raw_spans = build_conversation_render_spans(raw_messages)
        self.assertEqual(raw_spans[-1], {"start": 2, "end": 3})

        self.state.history_load_token = 17
        self.state.history_loading = True
        with (
            patch.object(
                self.window,
                "_normalize_and_persist_session_messages",
                return_value=normalized_messages,
            ),
            patch.object(self.window, "_render_initial_session_history") as render_history,
            patch("main.log_ui_navigation") as navigation_log,
        ):
            self.window._handle_session_history_loaded(
                {
                    "ok": True,
                    "session_id": self.state.session_id,
                    "token": 17,
                    "conversation_meta": {},
                    "conversation_record": {"status": "draft"},
                    "messages": raw_messages,
                    "agents": [],
                    "spans": raw_spans,
                    "elapsed_ms": 1,
                }
            )

        self.assertEqual(self.state.messages, normalized_messages)
        self.assertEqual(
            self.state.render_items,
            [{"start": 0, "end": 1}, {"start": 1, "end": 2}],
        )
        render_history.assert_called_once()
        normalized_log = next(
            call
            for call in navigation_log.call_args_list
            if call.args and call.args[0] == "history_load_normalized"
        )
        self.assertEqual(normalized_log.kwargs["raw_message_count"], 3)
        self.assertEqual(normalized_log.kwargs["normalized_message_count"], 2)
        self.assertTrue(normalized_log.kwargs["spans_in_bounds"])

    def test_chat_save_request_uses_shared_persistence_filter(self):
        self.state.messages = [
            {"id": "u1", "role": "user", "content": "问题"},
            {
                "id": "skill-context",
                "role": "system",
                "content": "runtime only",
                "meta": {
                    "kind": "skill_context_update",
                    "source": "selected_skill_prompt",
                },
            },
            {"id": "a1", "role": "assistant", "content": "回答"},
        ]

        with patch("main.log_ui_navigation") as navigation_log:
            request = self.window._build_chat_save_request(self.state, revision=3)

        self.assertEqual(
            [message["id"] for message in request.messages],
            ["u1", "a1"],
        )
        filter_log = next(
            call
            for call in navigation_log.call_args_list
            if call.args and call.args[0] == "chat_persistence_filter_applied"
        )
        self.assertEqual(filter_log.kwargs["filtered_message_count"], 1)

    def test_office_history_process_is_created_only_after_expand(self):
        messages = [
            {
                "id": "office-user",
                "role": "user",
                "content": "生成报告",
                "meta": {
                    "workflow_mode": "office_html_first",
                    "office_output_profile": "free",
                },
            },
            {"id": "office-final", "role": "assistant", "content": "报告已生成"},
        ]
        self.state.messages = messages
        self.state.render_items = [{"start": 0, "end": len(messages)}]

        self.window._render_session_history_spans(self.state, self.state.render_items)

        card = self.state.chat_layout.itemAt(0).widget()
        self.assertIsInstance(card, OfficeDraftTaskCard)
        self.assertEqual(card.process_widget_count(), 0)
        self.assertFalse(self.state.render_nodes["office:office-user"]["details_materialized"])

        card.toggle_btn.setChecked(True)
        for _ in range(10):
            self.app.processEvents()
            if getattr(card, "_history_process_materialized", False):
                break

        self.assertTrue(card._history_process_materialized)
        self.assertGreater(card.process_widget_count(), 0)
        self.assertTrue(self.state.render_nodes["office:office-user"]["details_materialized"])

    def test_edit_reuses_prefix_widgets_and_does_not_clear_history(self):
        messages = [
            {"id": "u1", "role": "user", "content": "第一问"},
            {"id": "a1", "role": "assistant", "content": "第一答"},
            {"id": "u2", "role": "user", "content": "第二问"},
            {"id": "a2", "role": "assistant", "content": "第二答"},
        ]
        self.state.messages = messages
        self.state.render_items = build_conversation_render_spans(messages)
        self.state.displayed_render_count = len(self.state.render_items)
        self.window._render_session_history_spans(self.state, self.state.render_items)
        prefix_widget = self.state.render_node_by_message_id["u1"]["widget"]

        def submit(state, text, prompt_files, **_kwargs):
            message = {"id": "u2-new", "role": "user", "content": text}
            state.messages.append(message)
            self.window.add_chat_bubble(
                "User",
                text,
                animate=False,
                source_message_id="u2-new",
                session_id=state.session_id,
            )
            return True

        with (
            patch.object(self.window, "clear_chat_layout", side_effect=AssertionError("full replay")),
            patch.object(self.window, "_submit_session_request", side_effect=submit),
            patch.object(self.window, "save_chat_history"),
            patch.object(self.window, "refresh_history_list"),
            patch("main.ProductMessageDialog.exec_result", return_value=QMessageBox.Yes),
        ):
            self.assertTrue(self.window.edit_user_message_inline(self.state.session_id, "u2", "修改后的第二问"))

        self.assertIs(self.state.render_node_by_message_id["u1"]["widget"], prefix_widget)
        self.assertNotIn("u2", {message.get("id") for message in self.state.messages})
        self.assertNotIn("a2", {message.get("id") for message in self.state.messages})
        self.assertEqual(self.state.messages[-1]["id"], "u2-new")
        self.assertTrue(self.state.messages[-1]["meta"]["edited"])

    def test_edit_submission_failure_restores_original_widgets_without_replay(self):
        messages = [
            {"id": "u1", "role": "user", "content": "第一问"},
            {"id": "a1", "role": "assistant", "content": "第一答"},
            {"id": "u2", "role": "user", "content": "第二问"},
            {"id": "a2", "role": "assistant", "content": "第二答"},
        ]
        self.state.messages = messages
        self.state.render_items = build_conversation_render_spans(messages)
        self.state.displayed_render_count = len(self.state.render_items)
        self.window._render_session_history_spans(self.state, self.state.render_items)
        original_widgets = [
            self.state.chat_layout.itemAt(index).widget()
            for index in range(self.state.chat_layout.count() - 1)
        ]

        with (
            patch.object(self.window, "clear_chat_layout", side_effect=AssertionError("full replay")),
            patch.object(self.window, "_submit_session_request", return_value=False),
            patch.object(self.window, "save_chat_history"),
            patch("main.ProductMessageDialog.exec_result", return_value=QMessageBox.Yes),
        ):
            self.assertFalse(self.window.edit_user_message_inline(self.state.session_id, "u2", "不会提交"))

        restored_widgets = [
            self.state.chat_layout.itemAt(index).widget()
            for index in range(self.state.chat_layout.count() - 1)
        ]
        self.assertEqual(restored_widgets, original_widgets)
        self.assertEqual([message["id"] for message in self.state.messages], ["u1", "a1", "u2", "a2"])

    def test_delete_user_message_keeps_downstream_widget_objects(self):
        messages = [
            {"id": "u1", "role": "user", "content": "第一问"},
            {"id": "a1", "role": "assistant", "content": "第一答"},
            {"id": "u2", "role": "user", "content": "第二问"},
        ]
        self.state.messages = messages
        self.state.render_items = build_conversation_render_spans(messages)
        self.state.displayed_render_count = len(self.state.render_items)
        self.window._render_session_history_spans(self.state, self.state.render_items)
        downstream_widget = self.state.render_node_by_message_id["u2"]["widget"]

        with (
            patch("main.QMessageBox.question", return_value=QMessageBox.Yes),
            patch.object(self.window, "save_chat_history"),
            patch.object(self.window, "refresh_history_list"),
            patch.object(self.window, "clear_chat_layout", side_effect=AssertionError("full replay")),
        ):
            self.assertTrue(self.window.delete_user_message_in_place(self.state.session_id, "u1"))

        self.assertEqual([message["id"] for message in self.state.messages], ["a1", "u2"])
        self.assertIs(self.state.render_node_by_message_id["u2"]["widget"], downstream_widget)


if __name__ == "__main__":
    unittest.main()
