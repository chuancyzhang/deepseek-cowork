import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget

from core.conversation_render import build_conversation_render_spans, project_visible_messages
from core.message_persistence import project_provider_messages
from main import MainWindow


class HistoryPagingConsistencyTests(unittest.TestCase):
    """Exercise the real checkpoint/pager without starting workers or a daemon."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow.__new__(MainWindow)
        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.layout.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.container)
        self.state = SimpleNamespace(
            session_id="paging-test", messages=[], render_items=[], displayed_render_count=0,
            chat_layout=self.layout, chat_scroll=self.scroll,
            history_loaded=True, history_loading=False, history_load_token=3,
            history_page_token=3, history_page_queue=[], history_page_jump_target_id="",
            auto_loading_history=False, render_node_by_message_id={}, guidance_widgets={},
            active_turn_id=1, active_turn_request_id="run-1", active_agent_turn_group=None,
        )
        self.window.current_session_id = None
        self.window.get_session = lambda sid: self.state if sid == self.state.session_id else None
        self.window.get_current_session = lambda: self.state
        self.window.chat_storage = SimpleNamespace(
            normalize_messages=lambda messages, **kwargs: copy.deepcopy(messages),
        )
        self.window._sync_question_navigator = Mock()
        self.window._finalize_question_navigator_jump = Mock()
        self.window._question_jump_error = Mock()
        self.window.queue_session_bubble_virtualization = Mock()
        self.window._show_conversation_notice = Mock()
        self.window._render_session_history_spans = Mock(side_effect=self._render_spans)
        self.window._flush_ui_stream_buffers = Mock()
        self.window.flush_session_thinking = Mock()
        self.window.flush_session_content = Mock()
        self.window._timeline_close_open_events = Mock()
        self.window._timeline_has_running_tools = Mock(return_value=False)
        self.window._close_live_agent_stage = Mock(return_value=None)
        self.window._timeline_find_event = lambda state, **kw: self.events.get(kw["message_id"])
        self.window._timeline_append_event = self._append_event
        self.window._append_visible_assistant_stages = self._append_visible_stage
        self.window.add_turn_guidance_inline = lambda message, **kw: self._add_widget(message)
        self.window._append_live_thinking_segment = lambda state: setattr(
            state, "active_agent_turn_group", SimpleNamespace(group_id="continuation"),
        )
        self.window.request_session_scroll_to_bottom = Mock()
        self.window.save_chat_history = Mock()
        self.events = {}
        self.visible_stage = None
        self.rendered_starts = []
        self.fail_at = None
        for target in ("main.QTimer.singleShot", "main.log_ui_navigation", "main.log_sub_agent_runtime"):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.scroll.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def _add_widget(self, message, index=None):
        widget = QLabel(message["content"])
        widget.setObjectName(message["id"])
        self.layout.insertWidget(self.layout.count() - 1 if index is None else index, widget)
        return widget

    def _render_spans(self, state, spans, insert_index=None):
        inserted = 0
        for span in spans:
            if span["start"] == self.fail_at:
                raise RuntimeError("injected history span failure")
            self.rendered_starts.append(span["start"])
            for message in project_visible_messages(state.messages, **span):
                index = None if insert_index is None else insert_index + inserted
                self._add_widget(message, index)
                inserted += 1
        return inserted

    def _widgets(self):
        return [self.layout.itemAt(i).widget() for i in range(self.layout.count() - 1)]

    def _assert_visible_suffix(self):
        spans = self.state.render_items
        start = spans[len(spans) - self.state.displayed_render_count]["start"]
        expected = [m["id"] for m in project_visible_messages(self.state.messages, start=start)]
        self.assertEqual([w.objectName() for w in self._widgets()], expected)

    def _seed_history(self, turns=4, displayed=2):
        for turn in range(turns):
            self.state.messages.extend([
                {"id": f"u{turn}", "role": "user", "content": "相同问题"},
                {"id": f"a{turn}", "role": "assistant", "content": "相同回答"},
            ])
        self.window._rebuild_session_render_spans(self.state)
        self.state.displayed_render_count = displayed
        self._render_spans(self.state, self.state.render_items[-displayed:])
        self.rendered_starts.clear()

    def _live_append(self, message):
        previous_count = self.state.displayed_render_count
        previous_total = len(self.state.render_items)
        self.state.messages.append(message)
        self._add_widget(message)
        self.window._rebuild_session_render_spans(self.state)
        self.window._sync_displayed_render_count_after_live_append(
            self.state, previous_count, previous_total,
        )

    def _append_event(self, state, kind, **fields):
        event = {"kind": kind, **fields}
        self.events[event["message_id"]] = event
        return event

    def _append_visible_stage(self, state, **kwargs):
        state.messages.append(copy.deepcopy(self.visible_stage))

    def _guidance(self, message_id):
        self.visible_stage = {
            "id": f"stage-{message_id}", "role": "assistant", "content": "已显示的阶段正文",
            "meta": {"ui_visible_fragment": True},
        }
        self._add_widget(self.visible_stage)
        message = {"id": message_id, "role": "user", "content": "补充要求",
                   "meta": {"same_turn_guidance": True}}
        self.window._render_turn_guidance_checkpoint(self.state, message, message["content"], [])
        return message

    def _drain_page(self):
        for _ in range(len(self.state.render_items) + 1):
            if not self.state.auto_loading_history:
                return
            self.window._render_next_history_page_span(self.state.session_id, 3)
        self.fail("history page did not finish")

    def test_guided_turns_do_not_reload_already_visible_answers(self):
        for turn in range(1, 5):
            self._live_append({"id": f"u{turn}", "role": "user", "content": "提问"})
            for guidance in range({2: 3, 3: 1}.get(turn, 0)):
                self._guidance(f"g{turn}-{guidance}")
            self._live_append({"id": f"a{turn}", "role": "assistant", "content": "最终回答"})

        self.assertEqual(len(self.state.render_items), 8)
        self.assertEqual(self.state.displayed_render_count, 8)
        before = self._widgets()
        for _ in range(3):
            self.window.load_more_history(self.state.session_id)
            self._drain_page()
        self.assertEqual(self._widgets(), before)
        self.window._render_session_history_spans.assert_not_called()

    def test_guidance_preserves_real_unloaded_history(self):
        self._seed_history(turns=10, displayed=4)
        self._live_append({"id": "new-user", "role": "user", "content": "新问题"})
        first = self._guidance("first-guidance")
        self._guidance("second-guidance")
        before = copy.deepcopy(self.state.messages)
        # A repeated acknowledgement of the same guidance must not add another span.
        self.window._render_turn_guidance_checkpoint(self.state, first, first["content"], [])
        self.assertEqual(self.state.messages, before)
        self.assertEqual(len(self.state.render_items) - self.state.displayed_render_count, 16)

        provider_before = project_provider_messages(self.state.messages)
        existing_widgets = self._widgets()
        self.window.load_more_history(self.state.session_id)
        self._drain_page()
        self.assertEqual(len(self.state.render_items) - self.state.displayed_render_count, 4)
        self._assert_visible_suffix()
        self.assertEqual(self._widgets()[-len(existing_widgets):], existing_widgets)
        self.assertEqual(self.state.messages, before)
        self.assertEqual(project_provider_messages(self.state.messages), provider_before)

    def test_normal_turns_remain_fully_displayed(self):
        for turn in range(4):
            self._live_append({"id": f"u{turn}", "role": "user", "content": "提问"})
            self._live_append({"id": f"a{turn}", "role": "assistant", "content": "回答"})
        self.window.load_more_history(self.state.session_id)
        self.assertEqual(self.state.displayed_render_count, 8)
        self.window._render_session_history_spans.assert_not_called()

    def test_page_prepends_nearest_span_and_preserves_message_order(self):
        self._seed_history()
        self.window.load_more_history(self.state.session_id)
        self.window._render_next_history_page_span(self.state.session_id, 3)
        self.assertEqual(self.rendered_starts, [5])
        self.assertEqual([w.objectName() for w in self._widgets()], ["a2", "u3", "a3"])
        self._drain_page()
        self.assertEqual(self.rendered_starts, [5, 4, 3, 2, 1, 0])
        self._assert_visible_suffix()

    def _check_partial_failure(self, *, navigate=False):
        self._seed_history()
        messages_before = copy.deepcopy(self.state.messages)
        self.fail_at = 4
        if navigate:
            self.window.jump_to_question("u0")
        else:
            self.window.load_more_history(self.state.session_id)
        self._drain_page()
        self.assertEqual(self.state.displayed_render_count, 3)
        self.assertEqual(self.rendered_starts, [5])
        self.assertFalse(self.state.history_page_loading)
        self._assert_visible_suffix()
        self.window._show_conversation_notice.assert_called_once()
        existing_widgets = self._widgets()

        self.fail_at = None
        self.window.load_more_history(self.state.session_id)
        self._drain_page()
        self.assertEqual(self.rendered_starts, [5, 4, 3, 2, 1, 0])
        self.assertEqual(self.state.displayed_render_count, 8)
        self._assert_visible_suffix()
        self.assertEqual(self._widgets()[-len(existing_widgets):], existing_widgets)
        self.assertEqual(self.state.messages, messages_before)

    def test_partial_page_failure_retries_without_duplicates_or_gaps(self):
        self._check_partial_failure()

    def test_question_navigation_uses_same_failure_safe_page_order(self):
        self._check_partial_failure(navigate=True)

    def test_guidance_during_pending_page_keeps_rollback_count_correct(self):
        self._seed_history()
        self._live_append({"id": "live-user", "role": "user", "content": "继续"})
        self.window.load_more_history(self.state.session_id)
        self.window._render_next_history_page_span(self.state.session_id, 3)
        self._guidance("during-page")
        self.fail_at = 4
        self._drain_page()
        self.assertEqual(self.state.displayed_render_count, 5)
        self._assert_visible_suffix()
        self.fail_at = None
        self.window.load_more_history(self.state.session_id)
        self._drain_page()
        self._assert_visible_suffix()

    def test_stale_page_callback_does_not_change_visible_messages(self):
        self._seed_history()
        self.window.load_more_history(self.state.session_id)
        before = self._widgets()
        queue_before = list(self.state.history_page_queue)
        self.window._render_next_history_page_span(self.state.session_id, 2)
        self.assertEqual(self._widgets(), before)
        self.assertEqual(self.state.history_page_queue, queue_before)


if __name__ == "__main__":
    unittest.main()
