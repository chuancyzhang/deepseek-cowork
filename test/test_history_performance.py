import os
import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtTest import QTest

from core.conversation_render import build_conversation_render_spans
from ui.history_presentation import RUN_ELAPSED_META_KEY, history_elapsed_seconds
from ui.workspace_presentation import ConversationRunNotice
from ui_window_cleanup import close_test_window
from main import (
    AssistantTurnGroup,
    ChatBubble,
    HistoricalAssistantSummary,
    MainWindow,
    OfficeDraftTaskCard,
    SessionHistoryLoadWorker,
    ThinkingTextView,
)


class HistoryPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary_root = Path(__file__).resolve().parents[1] / ".tmp" / "history-loading-tests"
        temporary_root.mkdir(parents=True, exist_ok=True)
        self.temporary = self.enterContext(tempfile.TemporaryDirectory(dir=temporary_root))
        self.enterContext(patch.dict(os.environ, {"APPDATA": self.temporary,
            "LOCALAPPDATA": self.temporary, "COWORK_EXTERNAL_BACKGROUND_SERVICES": "0"}))
        data = Path(self.temporary, "DeepSeekCowork")
        data.mkdir()
        (data / "config.json").write_text("{}", encoding="utf-8")
        self.enterContext(patch("main.get_app_data_dir", return_value=str(data)))
        self.enterContext(patch("core.config_manager.get_app_data_dir", return_value=str(data)))
        self.window = MainWindow()
        self.state = self.window.get_current_session()
        self.window.clear_chat_layout(self.state.chat_layout)
        self.state.empty_state = None
        self.state.history_loaded = True

    def tearDown(self):
        close_test_window(self.window)

    def test_reopened_history_navigation_without_prior_scroll(self):
        self.window._startup_hydration_scheduled = True
        self.window._background_services_scheduled = True
        self.window._model_onboarding_shown = True
        self.state.messages = [
            {"id": f"{role}{i}", "role": role,
             "content": f"第 {i + 1} 轮{'提问' if role == 'user' else '回答'}\n\n" + "历史正文。" * 80}
            for i in range(10) for role in ("user", "assistant")
        ]
        self.assertTrue(self.window.save_chat_history(self.state.session_id, flush=True))
        self.window.resize(1280, 800)
        self.window.show()

        def wait_until(predicate):
            deadline = time.monotonic() + 10
            while not predicate() and time.monotonic() < deadline:
                QTest.qWait(20)
            self.assertTrue(predicate())
            QTest.qWait(150)

        for entry in ("directory", "rail", "scroll", "search"):
            with self.subTest(entry=entry):
                self.window._close_conversation_search()
                self.window.queue_session_history_load(self.state.session_id)
                wait_until(lambda: self.state.history_loaded and not self.state.history_loading
                           and self.state.history_loading_widget is None)
                self.assertGreater(self.state.history_load_token, 0)
                self.assertEqual(len(self.window._question_navigator_entries_for_state(self.state)), 10)
                self.assertNotIn("user0", self.state.render_node_by_message_id)
                before = copy.deepcopy(self.state.messages)
                if entry == "directory":
                    from ui.primitives import ProductPopover
                    anchor = next(button for button in self.state.session_widget.findChildren(QPushButton)
                                  if button.accessibleName() == "历史提问")
                    self.window._show_question_directory(anchor)
                    popover = next(p for p in self.window.findChildren(ProductPopover) if p.isVisible())
                    next(b for b in popover.findChildren(QPushButton) if b.text().startswith("1.")).click()
                elif entry == "rail":
                    self.window.question_navigator_rail.jumpRequested.emit("user0")
                elif entry == "search":
                    self.window._open_conversation_search()
                    self.window.conversation_search.input.setText("第 1 轮提问")
                    wait_until(lambda: self.window._conversation_search_highlight is not None)
                else:
                    self.state.chat_scroll.verticalScrollBar().setValue(0)
                wait_until(lambda: "user0" in self.state.render_node_by_message_id
                           and not self.state.auto_loading_history)
                self.assertFalse(self.state.history_page_loading)
                self.assertFalse(self.state.history_page_jump_target_id)
                self.assertEqual(self.state.messages, before)
                if entry != "scroll":
                    target = self.state.render_node_by_message_id["user0"]["widget"]
                    y = target.mapTo(self.state.chat_scroll.viewport(), QPoint()).y()
                    self.assertGreaterEqual(y, -5)
                    self.assertLess(y, self.state.chat_scroll.viewport().height())
                # Also check a middle question: reaching the top alone would
                # not prove that navigation uses the requested message anchor.
                self.window._close_conversation_search()
                for message_id in ("user7", "user2"):
                    self.window.jump_to_question(message_id)
                    QTest.qWait(200)
                    target = self.state.render_node_by_message_id[message_id]["widget"]
                    y = target.mapTo(self.state.chat_scroll.viewport(), QPoint()).y()
                    self.assertAlmostEqual(y, 20, delta=5)

    def test_history_reset_releases_pending_page_and_jump(self):
        self.state.auto_loading_history = True
        self.state.history_page_loading = True
        self.state.history_page_queue = [{"start": 0, "end": 1}]
        self.state.history_page_jump_target_id = "old-question"
        self.state.history_page_anchor_widget = self.state.chat_scroll.widget()
        self.window._reset_session_history_state(self.state)
        self.assertFalse(self.state.auto_loading_history)
        self.assertFalse(self.state.history_page_loading)
        self.assertEqual(self.state.history_page_queue, [])
        self.assertEqual(self.state.history_page_jump_target_id, "")
        self.assertIsNone(self.state.history_page_anchor_widget)

    def test_recorded_run_time_survives_save_without_changing_messages(self):
        self.state.messages = [{"id": "timed-reply", "role": "assistant", "content": "回答",
                                "meta": {"request_id": "timed-run"}}]
        before = copy.deepcopy(self.state.messages)
        self.state.active_turn_request_id = "timed-run"
        self.state.live_run_started_at = 100.0
        with patch("main.time.monotonic", return_value=219.0):
            self.window._finalize_live_turn_process_groups(self.state)
        self.assertTrue(self.window.save_chat_history(self.state.session_id, flush=True))
        stored = self.window.chat_storage.get_conversation_meta(self.state.session_id)
        self.assertEqual(history_elapsed_seconds(self.state.messages, stored), 119.0)
        self.assertEqual(self.state.messages, before)
        # Repeated terminal handling cannot replace the recorded duration.
        self.window._finalize_live_turn_process_groups(self.state)
        self.assertEqual(self.state.persisted_conversation_meta[RUN_ELAPSED_META_KEY],
                         {"timed-run": 119.0})

    def test_history_time_requires_exact_recorded_run(self):
        messages = [{"role": "assistant", "meta": {"request_id": "run-one"}}]
        meta = {RUN_ELAPSED_META_KEY: {"run-one": 119, "run-two": 8}}
        self.assertEqual(history_elapsed_seconds(messages, meta), 119)
        self.assertIsNone(history_elapsed_seconds(messages, {}))
        self.assertIsNone(history_elapsed_seconds([{"role": "assistant"}], meta))
        messages.append({"role": "assistant", "meta": {"request_id": "run-two"}})
        self.assertIsNone(history_elapsed_seconds(messages, meta))
        for value in (True, -1, float("nan"), float("inf"), "119"):
            with self.subTest(value=value):
                self.assertIsNone(history_elapsed_seconds(messages[:1],
                    {RUN_ELAPSED_META_KEY: {"run-one": value}}))

    def test_history_overlay_survives_reset_and_stale_reveal(self):
        self.state.history_load_token = 30
        self.state.history_loading = True
        self.state.history_loaded = False
        self.window._show_session_loading_state(self.state)
        overlay = self.state.history_loading_widget
        self.assertEqual(self.state.chat_layout.indexOf(overlay), -1)
        self.window._reset_session_history_state(self.state)
        self.assertIs(self.state.history_loading_widget, overlay)
        self.assertFalse(overlay.isHidden())
        self.assertFalse(self.state.chat_layout.parentWidget().isEnabled())
        self.window._reveal_session_history(self.state.session_id, 29)
        self.window._reveal_session_history(self.state.session_id, 30)
        self.assertIs(self.state.history_loading_widget, overlay)
        self.state.history_loading = False
        self.state.history_loaded = True
        other_id = self.window.create_new_session(make_current=False)
        self.window.set_current_session(other_id)
        with patch.object(self.window, "_restore_session_scroll_position") as restore:
            self.window._reveal_session_history(self.state.session_id, 30)
            restore.assert_not_called()
        self.assertIsNone(self.state.history_loading_widget)
        self.assertTrue(self.state.chat_layout.parentWidget().isEnabled())
        self.assertEqual(self.window.current_session_id, other_id)

    def test_initial_render_failure_removes_overlay_and_preserves_messages(self):
        self.state.history_load_token = 31
        self.state.history_loading = True
        self.state.history_loaded = False
        self.state.messages = [{"id": "u-render-error", "role": "user", "content": "已保存的提问"}]
        self.state.history_initial_render_queue = [{"start": 0, "end": 1}]
        self.state.history_initial_render_inserted = 0
        before = copy.deepcopy(self.state.messages)
        self.window._show_session_loading_state(self.state)
        with patch.object(self.window, "_render_session_history_spans", side_effect=RuntimeError("渲染失败")):
            self.window._render_next_initial_history_span(self.state.session_id, 31)
        self.assertIsNone(self.state.history_loading_widget)
        self.assertFalse(self.state.history_loading)
        self.assertFalse(self.state.history_loaded)
        self.assertEqual(self.state.messages, before)
        notice = self.state.chat_layout.itemAt(0).widget()
        from PySide6.QtWidgets import QPushButton
        retry = next(button for button in notice.findChildren(QPushButton) if button.text() == "重试")
        with patch.object(self.window, "queue_session_history_load") as load:
            retry.click()
            load.assert_called_once_with(self.state.session_id)

    def test_timed_history_disclosure_keeps_final_reply_and_existing_thoughts(self):
        self.state.persisted_conversation_meta = {RUN_ELAPSED_META_KEY: {"run-timed": 119}}
        self.state.messages = [{"id": "a-timed", "role": "assistant", "content": "最终答复",
                                "reasoning_content": "保留的思考原文",
                                "meta": {"request_id": "run-timed", "ui_reply_kind": "final"}}]
        self.window._render_session_history_spans(self.state, [{"start": 0, "end": 1}])
        summary = self.state.history_detail_summaries[0]
        self.assertEqual(summary.detail_button.text(), "用时 1分钟 59秒")
        summary.detail_button.click()
        for _ in range(20):
            self.app.processEvents()
            if summary.detail_groups and not summary.detail_loading:
                break
        self.assertTrue(summary.detail_groups)
        self.assertTrue(summary.body.isVisibleTo(summary))
        bubble = summary.detail_groups[0].stage_bubbles[0]
        self.assertEqual(bubble.findChild(ThinkingTextView).text(), "保留的思考原文")
        summary.detail_button.click()
        self.assertTrue(summary.body.isVisibleTo(summary))

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
        node = self.state.render_node_by_message_id["a0"]
        self.assertIs(node["widget"], summary)
        self.assertFalse(node["details_materialized"])
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
        self.assertTrue(node["details_materialized"])
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

    def test_history_worker_native_finish_exits_loading_state(self):
        class SignalStub:
            def __init__(self):
                self.callbacks = []

            def connect(self, callback, *_args):
                self.callbacks.append(callback)

            def emit(self, *args):
                for callback in list(self.callbacks):
                    callback(*args)

        class WorkerStub:
            def __init__(self):
                self.finished_signal = SignalStub()
                self.finished = SignalStub()
                self.start = MagicMock()
                self.deleteLater = MagicMock()

        worker = WorkerStub()
        self.state.history_load_token = 23
        self.state.history_loading = True
        with (
            patch("main.SessionHistoryLoadWorker", return_value=worker),
            patch.object(self.window, "_show_session_load_error_state") as show_error,
        ):
            self.window._load_session_history(self.state.session_id, 23)
            worker.finished.emit()

        self.assertFalse(self.state.history_loading)
        self.assertFalse(self.state.history_loaded)
        show_error.assert_called_once()
        worker.deleteLater.assert_called_once()

    def test_queued_duplicate_user_projection_creates_one_widget(self):
        message = {"id": "u-reentry", "role": "user", "content": "第一条"}
        self.state.messages = [message]
        self.state.render_items = build_conversation_render_spans(self.state.messages)
        reentered = False

        def reenter_once():
            nonlocal reentered
            reentered = True
            self.window.render_message_batch([message], self.state.session_id, animate=False)

        # Rendering no longer pumps a nested event loop. A queued repeat must still deduplicate.
        QTimer.singleShot(0, reenter_once)
        inserted = self.window.render_message_batch([message], self.state.session_id, animate=False)
        self.app.processEvents()

        widgets = [
            self.state.chat_layout.itemAt(index).widget()
            for index in range(self.state.chat_layout.count())
        ]
        self.assertTrue(reentered)
        self.assertEqual(inserted, 1)
        self.assertEqual(len([widget for widget in widgets if isinstance(widget, ChatBubble)]), 1)

    def test_same_user_text_with_different_ids_creates_two_widgets(self):
        messages = [
            {"id": "u-same-1", "role": "user", "content": "相同内容"},
            {"id": "u-same-2", "role": "user", "content": "相同内容"},
        ]
        self.state.messages = messages

        inserted = self.window.render_message_batch(
            messages,
            self.state.session_id,
            animate=False,
        )

        self.assertEqual(inserted, 2)
        self.assertIsNot(
            self.state.render_node_by_message_id["u-same-1"]["widget"],
            self.state.render_node_by_message_id["u-same-2"]["widget"],
        )

    def test_repeated_history_user_span_does_not_change_layout_or_count(self):
        message = {"id": "u-history", "role": "user", "content": "历史问题"}
        self.state.messages = [message]
        spans = build_conversation_render_spans(self.state.messages)

        first_inserted = self.window._render_session_history_spans(self.state, spans)
        first_layout_count = self.state.chat_layout.count()
        second_inserted = self.window._render_session_history_spans(self.state, spans)

        self.assertEqual(first_inserted, 1)
        self.assertEqual(second_inserted, 0)
        self.assertEqual(self.state.chat_layout.count(), first_layout_count)

    def test_history_load_preserves_hidden_ledger_and_replays_visible_spans(self):
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
        normalized_messages = list(raw_messages)
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
            [{"start": 0, "end": 1}, {"start": 2, "end": 3}],
        )
        render_history.assert_called_once()
        normalized_log = next(
            call
            for call in navigation_log.call_args_list
            if call.args and call.args[0] == "history_load_normalized"
        )
        self.assertEqual(normalized_log.kwargs["raw_message_count"], 3)
        self.assertEqual(normalized_log.kwargs["normalized_message_count"], 3)
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
            ["u1", "skill-context", "a1"],
        )
        self.assertFalse(any(
            call.args and call.args[0] == "chat_persistence_filter_applied"
            for call in navigation_log.call_args_list
        ))

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
        # This fixture stubs submission/persistence; keep it stubbed through shutdown.
        self.enterContext(patch.object(self.window, "save_chat_history", return_value=True))
        messages = [
            {"id": "u1", "role": "user", "content": "第一问"},
            {"id": "a1", "role": "assistant", "content": "第一答"},
            {"id": "u2", "role": "user", "content": "第二问"},
            {"id": "a2", "role": "assistant", "content": "第二答"},
        ]
        self.state.messages = messages
        self.window.chat_storage.save_conversation_safely(
            self.state.session_id,
            messages,
            title="历史编辑测试",
            meta={"history_save_revision": 0},
        )
        self.state.render_items = build_conversation_render_spans(messages)
        self.state.displayed_render_count = len(self.state.render_items)
        self.window._render_session_history_spans(self.state, self.state.render_items)
        prefix_widget = self.state.render_node_by_message_id["u1"]["widget"]

        def submit(state, text, prompt_files, **_kwargs):
            message = {
                "id": "u2-new",
                "role": "user",
                "content": text,
                "meta": dict(_kwargs.get("user_message_meta") or {}),
            }
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
        self.enterContext(patch.object(self.window, "save_chat_history", return_value=True))
        messages = [
            {"id": "u1", "role": "user", "content": "第一问"},
            {"id": "a1", "role": "assistant", "content": "第一答"},
            {"id": "u2", "role": "user", "content": "第二问"},
            {"id": "a2", "role": "assistant", "content": "第二答"},
        ]
        self.state.messages = messages
        self.window.chat_storage.save_conversation_safely(
            self.state.session_id,
            messages,
            title="历史回滚测试",
            meta={"history_save_revision": 0},
        )
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
        self.assertEqual([widget for widget in restored_widgets if not isinstance(widget, ConversationRunNotice)],
                         original_widgets)
        self.assertEqual(sum(isinstance(widget, ConversationRunNotice) for widget in restored_widgets), 1)
        self.assertEqual([message["id"] for message in self.state.messages], ["u1", "a1", "u2", "a2"])

    def test_delete_user_message_keeps_downstream_widget_objects(self):
        messages = [
            {"id": "u1", "role": "user", "content": "第一问"},
            {"id": "a1", "role": "assistant", "content": "第一答"},
            {"id": "u2", "role": "user", "content": "第二问"},
        ]
        self.state.messages = messages
        self.window.chat_storage.save_conversation_safely(
            self.state.session_id,
            messages,
            title="历史删除测试",
            meta={"history_save_revision": 0},
        )
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


    def test_recovery_does_not_reload_loaded_session_history(self):
        self.state.history_loaded = True
        self.state.history_loading = False
        with (
            patch.object(self.window.chat_recovery_journal, "recover_into", return_value=([self.state.session_id], [])),
            patch.object(self.window, "queue_session_history_load") as queue_load,
            patch.object(self.window, "refresh_history_list"),
        ):
            self.window._apply_runtime_recovery_scan({})
        queue_load.assert_not_called()
        self.assertTrue(self.state.history_loaded)

    def test_stale_initial_history_render_token_is_ignored(self):
        self.state.history_load_token = 2
        self.state.history_initial_render_token = 1
        self.state.history_initial_render_queue = [{"start": 0, "end": 1}]
        self.window._render_next_initial_history_span(self.state.session_id, 1)
        self.assertEqual(len(self.state.history_initial_render_queue), 1)

    def test_stale_history_page_render_token_is_ignored(self):
        self.state.history_load_token = 8
        self.state.history_page_token = 7
        self.state.history_page_queue = [{"start": 0, "end": 1}]
        self.window._render_next_history_page_span(self.state.session_id, 7)
        self.assertEqual(len(self.state.history_page_queue), 1)

if __name__ == "__main__":
    unittest.main()
