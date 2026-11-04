"""Regression coverage for original text, replay offsets and visible failures."""
import copy
import os
import tempfile
import time
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("COWORK_EXTERNAL_BACKGROUND_SERVICES", "0")

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from core.conversation_render import project_visible_messages
from core.runtime_journal import RuntimeJournal
from main import ChatBubble, DaemonStreamWorker, MainWindow
from ui_window_cleanup import close_test_window


class ConversationReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Runtime journal uses hashed session/run names; avoid nested test roots
        # crossing Windows path limits while keeping all data in the task folder.
        root = Path(__file__).resolve().parents[1] / ".tmp" / "three-issues-investigation"
        root.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix="ui-", dir=root)
        self.addCleanup(directory.cleanup)
        for name in ("get_app_data_dir", "get_base_dir"):
            patcher = patch("core.config_manager." + name, return_value=directory.name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_freezing_keeps_indentation_and_newlines(self):
        bubble = ChatBubble("Agent", "")
        self.addCleanup(bubble.deleteLater)
        raw = "    code\n        nested\n\n"
        bubble.set_main_content(raw, final=False)
        self.assertEqual(bubble.freeze_content_fragment(), raw)
        self.assertEqual(bubble.main_content_text, raw)

    def test_reopen_replays_only_unseen_raw_suffix_and_rejects_missing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = RuntimeJournal(directory)
            journal.begin_run("s", "r", writer_owner="test")
            raw = "  first\n    second\nremaining"
            for delta in [raw[:4], raw[4:13], raw[13:]]:
                journal.append_event("s", "r", "content", {"type": "content", "delta": delta,
                                                          "source_message_id": "source"})
            state = SimpleNamespace(session_id="s", active_turn_request_id="r", messages=[
                {"id": "f1", "role": "assistant", "content": "first", "meta": {
                    "ui_visible_fragment": True, "ui_source_message_id": "source", "ui_stage_id": "s1"}},
                {"id": "f2", "role": "assistant", "content": "second", "meta": {
                    "ui_visible_fragment": True, "ui_source_message_id": "source", "ui_stage_id": "s2"}},
                {"id": "other", "role": "assistant", "content": "different", "meta": {
                    "ui_visible_fragment": True, "ui_source_message_id": "other-source"}}])
            before = copy.deepcopy(state.messages)
            window = SimpleNamespace(runtime_journal=journal)
            offset, snapshots = MainWindow._replay_source_content_cursor(window, state, "source")
            self.assertEqual(snapshots, 0)
            self.assertEqual(raw[offset:], "\nremaining")
            bubble = SimpleNamespace(ui_source_message_id="source", ui_stage_id="active")
            state.current_content_buffer = "\nremaining"
            self.assertEqual(MainWindow._visible_source_tail(window, state, raw, bubble), "\nremaining")
            self.assertEqual(state.messages, before)
            state.messages.append({"id": "source", "role": "assistant", "content": raw})
            self.assertEqual(MainWindow._replay_source_content_cursor(window, state, "source"), (len(raw), 0))
            with patch.object(journal, "read_events", return_value=[]):
                with self.assertRaises(ValueError):
                    MainWindow._replay_source_content_cursor(window, state, "source")
            with patch.object(journal, "read_events", side_effect=OSError("unreadable")):
                with self.assertRaises(OSError):
                    MainWindow._replay_source_content_cursor(window, state, "source")
            state.active_turn_request_id = "other-run"
            with self.assertRaises(ValueError):
                MainWindow._replay_source_content_cursor(window, state, "source")

    def _replay_window(self, messages, recorded):
        with patch.object(MainWindow, "start_background_services", lambda self: None):
            window = MainWindow()
        state = window.get_current_session()
        state.active_turn_id, state.active_turn_request_id, state.completed_turn_id = 1, "r", 0
        state.replaying_daemon_history = True
        state.daemon_running = True
        state.current_content_buffer = ""
        state.messages = copy.deepcopy(messages)
        worker = DaemonStreamWorker(Mock(), state.session_id, "", request_id="r", turn_id=1)
        state.daemon_worker = worker
        window._connect_daemon_worker_signals(state, worker, 1)
        journal = patch.object(window.runtime_journal, "read_events", return_value=[
            {"payload": {**payload, "source_message_id": "source"}} for payload in recorded])
        journal.start()
        self.addCleanup(journal.stop)

        def cleanup():
            state.daemon_running = False
            state.daemon_worker = None
            close_test_window(window)

        self.addCleanup(cleanup)
        return window, state, worker

    @staticmethod
    def _dispatch_replay(worker, sequence, payload):
        worker._dispatch_stream_message({**payload, "sequence": sequence})
        worker._ui_stream_buffer.flush()

    def test_snapshot_replay_skips_saved_canonical_and_keeps_identical_new_source(self):
        saved = [{"id": "source", "role": "assistant", "content": "Hello [[CW:person]]",
                  "meta": {"request_id": "r", "security_display_content": "Hello Alice"}}]
        recorded = [{"type": "content", "delta": "Hello "},
                    {"type": "content_snapshot", "content": "Hello [[CW:per"},
                    {"type": "content_snapshot", "content": "Hello Alice"}]
        window, state, worker = self._replay_window(saved, recorded)
        self._dispatch_replay(worker, 1, {"type": "observability", "data": {
            "type": "assistant_message_started", "message_id": "source"}})
        self.assertEqual((state.replay_content_skip, state.replay_snapshot_skip), (11, 2))
        for sequence, payload in enumerate(recorded, 2):
            self._dispatch_replay(worker, sequence, payload)
            self.assertEqual(state.current_content_buffer, "")
        self.assertEqual((state.replay_content_skip, state.replay_snapshot_skip), (0, 0))
        # Snapshots arriving after the initial journal scan still include the
        # saved canonical message in coverage, rather than showing it again.
        self._dispatch_replay(worker, 5, {"type": "content_snapshot", "content": "Hello Alice"})
        self.assertEqual(state.current_content_buffer, "")
        self.assertEqual(state.messages, saved)
        self._dispatch_replay(worker, 6, {"type": "observability", "data": {
            "type": "assistant_message_started", "message_id": "new-source"}})
        self._dispatch_replay(worker, 7, {"type": "content_snapshot", "content": "Hello Alice"})
        self.assertEqual(state.current_content_buffer, "Hello Alice")
        self.assertEqual(state.temp_thinking_bubble.main_content_text, "Hello Alice")

    def test_mixed_replay_uses_latest_snapshot_coordinates_before_skipping_deltas(self):
        saved = [{"id": "fragment", "role": "assistant", "content": "prefix shared",
                  "meta": {"ui_visible_fragment": True, "ui_source_message_id": "source",
                           "ui_stage_id": "old", "request_id": "r"}}]
        recorded = [{"type": "content", "delta": "long unrestored placeholder"},
                    {"type": "content_snapshot", "content": "different intermediate text"},
                    {"type": "content", "delta": " superseded"},
                    {"type": "content_snapshot", "content": "prefix"},
                    {"type": "content", "delta": " shared"},
                    {"type": "content", "delta": " NEW"}]
        window, state, worker = self._replay_window(saved, recorded)
        self._dispatch_replay(worker, 1, {"type": "observability", "data": {
            "type": "assistant_message_started", "message_id": "source"}})
        for sequence, payload in enumerate(recorded[:-1], 2):
            self._dispatch_replay(worker, sequence, payload)
            self.assertEqual(state.current_content_buffer, "")
            if sequence == 5:
                self.assertEqual(state.replay_content_skip, len(" shared"))
        self._dispatch_replay(worker, 7, recorded[-1])
        self.assertEqual(state.current_content_buffer, " NEW")
        self._dispatch_replay(worker, 8, {"type": "content_snapshot", "content": "prefix shared NEW done"})
        self.assertEqual(state.current_content_buffer, " NEW done")
        self.assertEqual(state.temp_thinking_bubble.main_content_text, " NEW done")
        self.assertEqual(state.messages, saved)

    def test_source_change_preserves_thinking_only_anchor_and_pending_text(self):
        window, state, worker = self._replay_window([], [])
        self._dispatch_replay(worker, 1, {"type": "observability", "data": {
            "type": "assistant_message_started", "message_id": "source"}})
        first = state.temp_thinking_bubble
        self._dispatch_replay(worker, 2, {"type": "thinking", "delta": "old thought"})
        self._dispatch_replay(worker, 3, {"type": "observability", "data": {
            "type": "assistant_message_started", "message_id": "new-source"}})
        self.assertIsNot(state.temp_thinking_bubble, first)
        self.assertEqual(first.ui_source_message_id, "source")
        self.assertEqual(first._thinking_text_length, len("old thought"))
        self.assertEqual(state.pending_thinking_delta, "")
        self._dispatch_replay(worker, 4, {"type": "content", "delta": "new body"})
        window.flush_session_content(state.session_id, final=True)
        window._append_visible_assistant_stages(state, turn_id=1, run_id="r", outcome="completed")
        fragments = [m for m in state.messages if (m.get("meta") or {}).get("ui_visible_fragment")]
        self.assertEqual([(m["meta"]["ui_source_message_id"], m["content"]) for m in fragments],
                         [("source", ""), ("new-source", "new body")])

    def test_legacy_stage_whitespace_survives_snapshot_replay_and_sqlite_reopen(self):
        meta = {"ui_visible_fragment": True, "ui_source_message_id": "source", "request_id": "r",
                "ui_turn_group_id": "group"}
        saved = [{"id": "u", "role": "user", "content": "question"},
                 {"id": "f1", "role": "assistant", "content": "first", "meta": {**meta, "ui_stage_id": "s1"}},
                 {"id": "guide", "role": "user", "content": "continue", "meta": {"same_turn_guidance": True}},
                 {"id": "f2", "role": "assistant", "content": "second", "meta": {**meta, "ui_stage_id": "s2"}}]
        raw = "  first\n    second\n    new"
        recorded = [{"type": "content_snapshot", "content": raw}]
        window, state, worker = self._replay_window(saved, recorded)
        self._dispatch_replay(worker, 1, {"type": "observability", "data": {
            "type": "assistant_message_started", "message_id": "source"}})
        self._dispatch_replay(worker, 2, recorded[0])
        self.assertEqual(state.current_content_buffer, "\n    new")
        ledger = state.messages + [{"id": "source", "role": "assistant", "content": raw,
                                    "meta": {"request_id": "r"}}]
        window.chat_storage.save_conversation_safely(state.session_id, ledger)
        reopened = window.chat_storage.get_messages(state.session_id)
        visible = project_visible_messages(reopened)
        self.assertEqual("".join(m["content"] for m in visible if m["role"] == "assistant"), raw)
        self.assertEqual([m["id"] for m in visible], ["u", "f1", "guide", "f2"])
        self.assertEqual([m for i in range(len(reopened)) for m in project_visible_messages(
            reopened, start=i, end=i + 1)], visible)

    def test_same_process_daemon_reconnect_retains_event_sequence(self):
        worker = DaemonStreamWorker(Mock(), "s", "", request_id="r")
        received = []
        worker.content_signal.connect(received.append)
        message = {"session_id": "s", "run_id": "r", "type": "content", "delta": "  raw\n", "sequence": 3}
        worker._dispatch_stream_message(message)
        worker._dispatch_stream_message(message)
        self.assertEqual(received, ["  raw\n"])
        self.assertEqual(worker._last_sequence, 3)
        with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
            worker._dispatch_stream_message({**message, "session_id": "other", "sequence": 4})

    def test_permission_copy_is_specific_and_other_403_is_not_relabelled(self):
        category, copy_text = MainWindow._run_failure_presentation(
            "403 Image generation is not enabled for this group", False, 0, 0)
        self.assertEqual(category, "provider_error")
        self.assertIn("当前分组未开通图片生成", copy_text)
        self.assertIn("图片理解无需关闭", copy_text)
        self.assertNotIn("图片生成", MainWindow._run_failure_presentation("403 forbidden", False, 0, 0)[1])

    def test_recovery_conflict_discards_queued_replay_and_keeps_retry_visible(self):
        with patch.object(MainWindow, "start_background_services", lambda self: None):
            window = MainWindow()
        try:
            state = window.get_current_session()
            state.active_turn_id, state.active_turn_request_id, state.completed_turn_id = 1, "r", 0
            state.replaying_daemon_history = True
            state.daemon_running = True
            state.current_content_buffer = "already visible"
            state.messages = [{"id": "fragment", "role": "assistant", "content": "already visible",
                "meta": {"ui_visible_fragment": True, "ui_source_message_id": "source"}}]
            before = copy.deepcopy(state.messages)
            worker = DaemonStreamWorker(Mock(), state.session_id, "", request_id="r", turn_id=1)
            state.daemon_worker = worker
            window._connect_daemon_worker_signals(state, worker, 1)
            with patch.object(window.runtime_journal, "read_events", return_value=[]):
                worker._dispatch_stream_message({"type": "observability", "sequence": 1,
                    "data": {"type": "assistant_message_started", "message_id": "source"}})
                worker._dispatch_stream_message({"type": "content", "sequence": 2, "delta": "duplicate replay"})
                worker._ui_stream_buffer.flush()
            self.assertTrue(worker._aborted)
            self.assertEqual(state.current_content_buffer, "already visible")
            self.assertEqual(state.messages, before)
            self.assertEqual(state.daemon_transport_state, "disconnected")
            self.assertIn("无法准确对齐", state.conversation_notice.label.text())
            self.assertEqual(state.conversation_notice.action_button.text(), "重新连接")
        finally:
            state.daemon_running = False
            state.daemon_worker = None
            close_test_window(window)

    def test_provider_conflict_keeps_ui_original_and_never_executes_draft_tool(self):
        from core.agent import LLMWorker
        from core.execution_authorization import ExecutionAuthorization
        from test_agent_bootstrap import ConfigStub, SkillStub
        from test_deepseek_provider import TestOpenAIProviderDeepSeek
        provider, client = TestOpenAIProviderDeepSeek._build_provider(self,
            base_url="https://example.test/v1", model_name="test-model", api_protocol="responses")
        raw = "    received body\n"
        client.responses.create.return_value = [
            SimpleNamespace(type="response.output_text.delta", delta=raw),
            SimpleNamespace(type="response.output_item.added", output_index=1, item=SimpleNamespace(
                type="function_call", call_id="call", name="read_file", arguments="")),
            SimpleNamespace(type="response.function_call_arguments.delta", output_index=1, delta="{}"),
            SimpleNamespace(type="response.completed", response=SimpleNamespace(id="resp", usage=None, output=[
                {"type": "message", "id": "m", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": "replacement"}]},
                {"type": "function_call", "call_id": "call", "name": "read_file", "arguments": "{}"}]))]
        with patch.object(MainWindow, "start_background_services", lambda self: None):
            window = MainWindow()
        try:
            state = window.get_current_session()
            window._retire_session_empty_state(state, reason="reliability_test")
            state.live_activity = True
            state.history_loaded = True
            state.history_loading = False
            state.active_turn_id, state.active_turn_request_id, state.completed_turn_id = 1, "r", 0
            state.active_turn_user_message_id = "u"
            state.messages = [{"id": "u", "role": "user", "content": "question"}]
            state.active_run_retry_context = {"source_run_id": "r", "source_user_message_id": "u",
                                              "original_user_text": "question", "submit_options": {}}
            window.chat_storage.save_conversation_safely(state.session_id, state.messages)
            window.runtime_journal.begin_run(state.session_id, "r", writer_owner="test", base_messages=state.messages)
            bubble = window._append_live_thinking_segment(state)
            skills, results = SkillStub(), []
            with tempfile.TemporaryDirectory() as directory, \
                 patch("core.agent.SkillManager", return_value=skills), \
                 patch("core.agent.LLMFactory.create_provider", return_value=provider), \
                 patch.object(LLMWorker, "_bind_agent_manager"), \
                 patch.object(LLMWorker, "_build_stable_system_prompt", return_value="stable"), \
                 patch.object(LLMWorker, "_build_runtime_context_prompt", return_value="runtime"), \
                 patch("core.agent.get_runtime_snapshot", return_value={}):
                worker = LLMWorker(copy.deepcopy(state.messages), ConfigStub(directory, {
                    "model_name": "test-model", "base_url": "https://example.test/v1", "api_protocol": "responses"}),
                    directory, session_id=state.session_id, turn_id=1, request_id="r",
                    execution_authorization=ExecutionAuthorization(state.session_id, "r", True, ""))
                worker.content_signal.connect(lambda text: window.handle_content_signal(text, state.session_id, 1, "r"))
                worker.finished_signal.connect(results.append)
                worker.run()
            self.assertEqual(state.current_content_buffer, raw)
            bubble = state.temp_thinking_bubble
            self.assertEqual(skills.calls, [])
            self.assertEqual(len(results), 1)
            self.assertIn("最终正文与已接收内容不一致", results[0]["error"])
            self.assertFalse(any(m.get("tool_calls") for m in results[0].get("generated_messages", [])))
            with patch.object(window, "save_chat_history", return_value=True):
                window.handle_llm_response(results[0], state.session_id, 1, "r")
                deadline = time.monotonic() + 5
                while state.completed_turn_id != 1 and time.monotonic() < deadline:
                    QTest.qWait(10)
            self.assertEqual(state.completed_turn_id, 1)
            self.assertEqual(bubble.main_content_text, raw)
            self.assertEqual([m["content"] for m in state.messages if m["role"] == "assistant"], [raw])
            self.assertIn("已有内容已保留", state.conversation_notice.label.text())
            self.assertFalse(state.conversation_notice.action_button.isHidden())
            window.chat_storage.save_conversation_safely(state.session_id, state.messages)
            restored = window.chat_storage.get_messages(state.session_id)
            self.assertEqual([m["content"] for m in project_visible_messages(restored) if m["role"] == "assistant"], [raw])
            with patch.object(window, "_submit_session_request", return_value=True) as submit:
                submit.assert_not_called()
                state.conversation_notice.action_button.click()
                submit.assert_called_once()
                self.assertIn("question", submit.call_args.args[1])
        finally:
            close_test_window(window)


if __name__ == "__main__":
    unittest.main()
