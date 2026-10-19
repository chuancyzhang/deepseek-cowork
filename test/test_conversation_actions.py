"""Focused behavior checks; no visual or product acceptance tests."""

import copy
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QVBoxLayout, QWidget
from core.chat_storage import ChatStorage
from core.conversation_actions import (
    append_reference_context, clone_branch,
    regeneration_source, write_reference_snapshot,
)
from core.filesystem_ops import read_text_file
from core.runtime_journal import RuntimeJournal
from ui.conversation_actions import BranchWorker, ConversationActionsMixin, ConversationMentionPopover
from main import MainWindow


class ConversationActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_branch_progress_is_removed_on_success_and_failure(self):
        class Worker(QObject):
            completed = Signal(str)
            failed = Signal(str)
            finished = Signal()

            def start(self):
                pass

        for success in (True, False):
            with self.subTest(success=success):
                host = QWidget()
                state = SimpleNamespace(session_id="source", history_loaded=True,
                    messages=[{"id": "u", "role": "user", "content": "问题"},
                              {"id": "a", "role": "assistant", "content": "回答"}],
                    persisted_conversation_meta={}, chat_layout=QVBoxLayout(host),
                    conversation_notice=object())
                other_notice = state.conversation_notice
                window = SimpleNamespace(get_session=lambda sid: state,
                    _flush_ui_stream_buffers=MagicMock(), _resolved_session_title=lambda s: "原会话",
                    _compose_session_meta=lambda s: {}, _workspace_dir_for_state=lambda s: ".",
                    _session_workspace_source=lambda s: "project", chat_storage=MagicMock(),
                    _show_conversation_notice=MagicMock(), sync_current_session_state=MagicMock(),
                    refresh_history_list=MagicMock(), input_field=MagicMock(),
                    load_session=MagicMock(side_effect=lambda sid: self.assertEqual(state.chat_layout.count(), 0)))
                worker = Worker()
                window.chat_storage.get_team_space_binding.return_value = None
                with patch("ui.conversation_actions.BranchWorker", return_value=worker):
                    ConversationActionsMixin.branch_conversation(window, "source", "a")
                self.assertEqual(state.chat_layout.count(), 1)
                if success:
                    worker.completed.emit("fork")
                    window.load_session.assert_called_once_with("fork")
                else:
                    worker.failed.emit("copy failed")
                    window.load_session.assert_not_called()
                    window._show_conversation_notice.assert_called_once()
                worker.finished.emit()
                self.assertEqual(state.chat_layout.count(), 0)
                self.assertFalse(state.branch_in_progress)
                self.assertIs(state.conversation_notice, other_notice)
                host.deleteLater()

    def test_mention_picker_separates_reference_and_agent_selection(self):
        picker = ConversationMentionPopover(
            [{"id": "chat", "title": "同名", "updated_at": 0, "meta": {}}],
            [{"id": "agent", "name": "同名"}], "", None)
        selected = []
        picker.selected.connect(lambda kind, record: selected.append((kind, record["id"])))
        picker.search.setText("同名")
        self.assertEqual(picker.items.count(), 1)
        picker.choose(picker.items.item(0))
        picker.tabs.buttons["智能体"].click()
        self.assertEqual(picker.items.count(), 1)
        picker.choose(picker.items.item(0))
        self.assertEqual(selected, [("会话", "chat")])
        picker.apply_btn.click()
        self.assertEqual(selected, [("会话", "chat"), ("智能体", "agent")])
        picker.deleteLater()

    def test_expert_checks_are_single_selection_and_survive_search_until_apply(self):
        profiles = [{"id": "a", "name": "甲专家"}, {"id": "b", "name": "乙专家"}]
        picker = ConversationMentionPopover([], profiles, "", None, agents_only=True, selected_agent_id="a")
        selected = []
        picker.selected.connect(lambda kind, record: selected.append(record))
        self.assertEqual(picker.items.item(0).checkState(), Qt.Checked)
        self.assertFalse(picker.apply_btn.isEnabled())
        picker.items.item(1).setCheckState(Qt.Checked)
        self.assertEqual(picker.items.item(0).checkState(), Qt.Unchecked)
        self.assertEqual(selected, [])
        picker.search.setText("甲")
        self.assertEqual(picker.items.item(0).checkState(), Qt.Unchecked)
        self.assertEqual(picker.pending_agent_id, "b")
        picker.search.clear()
        self.assertEqual(picker.items.item(1).checkState(), Qt.Checked)
        picker.apply_btn.click()
        self.assertEqual(selected, [profiles[1]])
        picker.deleteLater()

    def test_expert_clear_is_only_committed_on_apply(self):
        profiles = [{"id": "a", "name": "甲专家"}]
        selected = []
        for apply in (False, True):
            picker = ConversationMentionPopover([], profiles, "", None, agents_only=True, selected_agent_id="a")
            picker.selected.connect(lambda kind, record: selected.append(record))
            picker.clear_btn.click()
            self.assertEqual(picker.items.item(0).checkState(), Qt.Unchecked)
            self.assertEqual(selected, [])
            (picker.apply_btn if apply else picker.cancel_btn).click()
            self.assertEqual(selected, [None] if apply else [])
            picker.deleteLater()

    def test_reference_snapshot_submission_failure_and_running_guidance(self):
        with tempfile.TemporaryDirectory() as root:
            snapshot = {"session_id": "source", "title": "资料会话", "captured_at": datetime.now().astimezone(),
                        "messages": [{"id": "u", "role": "user", "content": "原始资料"}],
                        "running": False, "turn_id": "", "timeline": [], "live_tools": {}}
            ref = write_reference_snapshot(snapshot, Path(root) / ".cowork" / "target")
            before = Path(ref["snapshot_path"]).read_bytes()
            snapshot["messages"][0]["content"] = "来源修改后的内容"
            self.assertEqual(before, Path(ref["snapshot_path"]).read_bytes())
            readable = read_text_file(root, ref["snapshot_path"])
            self.assertTrue(readable["ok"])
            plain = {"content": "请分析", "content_parts": [{"type": "text", "text": "请分析"}]}
            self.assertIs(append_reference_context(plain, []), plain)
            payload = append_reference_context(plain, [ref])
            self.assertNotIn("原始资料", payload["content"])
            self.assertFalse(any(part["type"] == "input_file" for part in payload["content_parts"]))
            self.assertEqual(payload["meta"]["conversation_refs"][0]["snapshot_path"], ref["snapshot_path"])
            with patch("core.conversation_actions.SNAPSHOT_PART_BYTES", 80):
                split = write_reference_snapshot(snapshot, root)
            self.assertTrue(split["snapshot_parts"])
            self.assertEqual("".join(Path(part).read_text(encoding="utf-8") for part in split["snapshot_parts"]),
                             Path(split["snapshot_path"]).read_text(encoding="utf-8"))

            state = SimpleNamespace(session_id="target", conversation_refs=[ref], submit_in_progress=False,
                                    stop_pending=False, composer_draft="保留输入")
            window = SimpleNamespace(
                prepare_conversation_references=MagicMock(side_effect=OSError("disk unavailable")),
                _show_conversation_notice=MagicMock(), _submit_session_request_once=MagicMock(),
            )
            self.assertFalse(MainWindow._submit_session_request(window, state, "保留输入", clear_current_input=True))
            self.assertEqual(state.composer_draft, "保留输入")
            self.assertEqual(state.conversation_refs, [ref])
            window._submit_session_request_once.assert_not_called()
            self.assertFalse(state.submit_in_progress)

            # Existing agent mentions are still parsed independently of reference chips.
            agent_window = SimpleNamespace(_available_agent_profiles=lambda: [{"id": "a", "name": "分析师"}])
            agents, text = MainWindow._extract_agent_mentions(agent_window, "@分析师 请分析")
            self.assertEqual([a["id"] for a in agents], ["a"])
            self.assertEqual(text, "请分析")

            accepted = []
            state = SimpleNamespace(session_id="target", active_turn_id=2, active_turn_request_id="run",
                                    selected_skill_names=[], persisted_conversation_meta={}, llm_worker=MagicMock(),
                                    daemon_running=False)
            state.llm_worker.isRunning.return_value = True
            state.llm_worker.steer.return_value = {"accepted": True}
            window = SimpleNamespace(
                _normalize_prompt_file_paths=lambda values: list(values),
                _speech_to_text_runtime_config=lambda: {},
                _ensure_vision_attachment_support=lambda *args, **kwargs: True,
                _ensure_speech_component_before_submit=lambda *args, **kwargs: True,
                _selected_model_supports_vision=lambda *args: False,
                _build_user_message_payload=lambda text, files, **kwargs: append_reference_context(
                    {"content": text, "display_content": text}, kwargs.get("conversation_refs")),
                _flush_ui_stream_buffers=lambda *args: None,
                _new_message_id=lambda: "guidance",
                _workbench_submission_context=lambda state: {},
                _accept_turn_guidance=lambda state, message, *args: accepted.append(message),
            )
            self.assertTrue(MainWindow._submit_turn_guidance(window, state, "继续参考", conversation_refs=[ref]))
            self.assertEqual(accepted[0]["meta"]["conversation_refs"], [ref])
            self.assertEqual(state.llm_worker.steer.call_args.args[0]["id"], "guidance")
            window._message_attachment_paths = lambda message: []
            edited = MainWindow._build_edited_guidance_message(window, state, accepted[0], "修改后的补充")
            self.assertEqual(edited["meta"]["conversation_refs"], [ref])
            self.assertIn(ref["snapshot_path"], edited["content"])

    def test_branch_boundary_independence_files_and_atomic_failure(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source"
            source.mkdir()
            attachment = source / "result.txt"
            attachment.write_text("成果", encoding="utf-8")
            db = ChatStorage(str(Path(root) / "history.sqlite"))
            messages = [
                {"id": "u", "role": "user", "content": "处理附件", "content_parts": [{"type": "text", "text": "处理附件"}, {"type": "input_file", "path": str(attachment)}], "meta": {"user_added_files": [str(attachment), str(source / "missing.md")], "conversation_refs": [{"session_id": "ref", "title": "参考", "snapshot_path": str(source / "missing-snapshot.txt")}]}},
                {"id": "call", "role": "assistant", "content": "", "tool_calls": [{"id": "tool", "type": "function", "function": {"name": "text_file_read", "arguments": "{}"}}]},
                {"id": "result", "role": "tool", "tool_call_id": "tool", "content": "成果"},
                {"id": "answer", "role": "assistant", "content": f"[成果]({attachment})", "meta": {"ui_source_message_id": "answer", "ui_turn_group_id": "group"}},
                {"id": "later", "role": "user", "content": "后续不继承"},
            ]
            db.save_conversation("source", messages, title="原会话", meta={"workspace_dir": str(source)})
            original = db.get_messages("source")
            branch, meta = clone_branch(original, {"selected_model_id": "model", "token_usage_summary": {"total": 99}}, "fork", "source", "原会话", "answer")
            self.assertEqual(len(branch), 4)
            self.assertFalse({m["id"] for m in branch} & {m["id"] for m in original})
            self.assertEqual(branch[-1]["meta"]["ui_source_message_id"], branch[-1]["id"])
            self.assertNotIn("token_usage_summary", meta)
            self.assertTrue(meta["manual_title"])
            target = Path(root) / "fork"
            meta.update(workspace_dir=str(target), workspace_source="chat")
            worker = BranchWorker(db, "fork", "原会话 · 分支", branch, meta, None)
            errors = []
            worker.failed.connect(errors.append)
            worker.run()
            self.assertEqual(errors, [])
            self.assertFalse(target.exists())
            fork = db.get_messages("fork")
            self.assertEqual(fork[-1]["content"], original[3]["content"])
            self.assertNotIn("user_added_files", fork[0]["meta"])
            self.assertEqual(fork[0]["meta"]["branch_historical_files"], ["result.txt", "missing.md"])
            self.assertEqual(fork[0]["content_parts"], [{"type": "text", "text": "处理附件"}])
            self.assertTrue(fork[0]["meta"]["conversation_refs"][0]["history_only"])
            self.assertNotIn("snapshot_path", fork[0]["meta"]["conversation_refs"][0])
            self.assertEqual(db.get_messages("source"), original)
            self.assertEqual(attachment.read_text(encoding="utf-8"), "成果")
            db.delete_conversation("source")
            attachment.unlink()
            self.assertEqual(db.get_messages("fork"), fork)

            # A database failure rolls back the conversation without creating files.
            failure_target = Path(root) / "failed"
            next_messages, next_meta = clone_branch(fork, {}, "failed", "fork", "分支", fork[-1]["id"])
            next_meta.update(workspace_dir=str(failure_target), workspace_source="chat")
            failed = BranchWorker(db, "failed", "失败分支", next_messages, next_meta, None)
            failed.failed.connect(errors.append)
            with self.assertLogs("ui.conversation_actions", level="ERROR"), patch.object(db, "_replace_messages_in_connection", side_effect=OSError("database write failed")):
                failed.run()
            self.assertFalse(db.has_conversation("failed"))
            self.assertFalse(failure_target.exists())
            self.assertTrue(errors)
            # Project forks intentionally keep the same directory without copying.
            project_messages, project_meta = clone_branch(original, {}, "project-fork", "source", "项目", "answer")
            project_meta.update(workspace_dir=str(source), workspace_source="project")
            project = BranchWorker(db, "project-fork", "项目分支", project_messages, project_meta, None)
            project.run()
            self.assertEqual(db.get_conversation_meta("project-fork")["workspace_dir"], str(source))
            self.assertEqual(db.get_messages("project-fork")[0]["content_parts"], original[0]["content_parts"])

    def test_regeneration_cancel_failure_and_original_request_identity(self):
        with tempfile.TemporaryDirectory() as root:
            db = ChatStorage(str(Path(root) / "history.sqlite"))
            messages = [{"id": "u", "role": "user", "content": "原始请求", "meta": {"workflow_mode": "office_html_first"}},
                        {"id": "g", "role": "user", "content": "本轮补充", "meta": {"same_turn_guidance": True}},
                        {"id": "a", "role": "assistant", "content": "旧回答"},
                        {"id": "later", "role": "user", "content": "后续讨论"}]
            self.assertEqual(regeneration_source(messages, "a")["id"], "u")
            db.save_conversation("session", messages)
            original = db.get_messages("session")
            state = SimpleNamespace(session_id="session", messages=copy.deepcopy(original), composer_draft="尚未发送的草稿",
                                    persisted_agents=[], render_node_by_message_id={}, last_history_rewrite_committed=False)
            window = SimpleNamespace(
                get_session=lambda sid: state,
                _can_rewrite_session_from_message=lambda s: (True, ""),
                _find_message_by_id=lambda rows, mid: next((m for m in rows if m["id"] == mid), None),
                _message_attachment_paths=lambda message: [],
                _model_profile_for_state=lambda s: {"id": "model"},
                prepare_conversation_references=lambda *args: [],
                save_chat_history=lambda **kwargs: True,
                _history_rewrite_guard=lambda s: {"messages_hash": RuntimeJournal.messages_hash(db.get_messages("session")), "revision": int(db.get_conversation_meta("session").get("history_save_revision") or 0)},
                _capture_history_rewrite=lambda s, mid: {"messages": copy.deepcopy(s.messages), "persisted_agents": []},
                _truncate_rewrite_state=lambda s, index: setattr(s, "messages", s.messages[:index]),
                _restore_history_rewrite=lambda s, snap: setattr(s, "messages", snap["messages"]),
                _commit_history_rewrite=MagicMock(),
                _show_conversation_notice=MagicMock(), refresh_history_list=MagicMock(),
                _restore_rewrite_viewport=MagicMock(), chat_storage=db,
            )
            window._submit_session_request = MagicMock(return_value=False)
            dialog = MagicMock()
            dialog.exec_result.return_value = QMessageBox.No
            with patch("main.ProductMessageDialog", return_value=dialog):
                self.assertFalse(MainWindow.edit_user_message_inline(window, "session", "u", "原始请求", regenerate=True))
            window._submit_session_request.assert_not_called()
            self.assertEqual(state.messages, original)
            dialog.exec_result.return_value = QMessageBox.Yes
            with patch("main.ProductMessageDialog", return_value=dialog):
                self.assertFalse(MainWindow.edit_user_message_inline(window, "session", "u", "原始请求", regenerate=True))
            self.assertEqual(state.messages, original)
            self.assertEqual(db.get_messages("session"), original)
            submitted_options = window._submit_session_request.call_args.kwargs
            self.assertEqual(submitted_options["history_source_message"]["id"], "u")
            self.assertEqual(submitted_options["workflow_mode"], "office_html_first")
            self.assertFalse(submitted_options["clear_current_input"])

            def commit(s, text, files, **options):
                s.messages.append(copy.deepcopy(options["history_source_message"]))
                guard = options["history_rewrite_guard"]
                db.rewrite_conversation_safely("session", s.messages, expected_messages_hash=guard["messages_hash"], expected_revision=guard["revision"])
                s.last_history_rewrite_committed = True
                return False  # the model failed to start after the durable replacement

            window._submit_session_request = commit
            with patch("main.ProductMessageDialog", return_value=dialog):
                self.assertFalse(MainWindow.edit_user_message_inline(window, "session", "u", "原始请求", regenerate=True))
            self.assertEqual([m["id"] for m in db.get_messages("session")], ["u"])
            self.assertEqual(state.composer_draft, "尚未发送的草稿")
            window._commit_history_rewrite.assert_called_once()

            # Reuse the real run identity gate without constructing the desktop.
            state.active_turn_id, state.active_turn_request_id = 2, "new-run"
            state.current_content_buffer = "新回答"
            state.tool_cards = {"tool": MagicMock()}
            window._event_matches_active_run = lambda s, turn, request: MainWindow._event_matches_active_run(window, s, turn, request)
            MainWindow.handle_content_signal(window, "迟到片段", "session", 1, "old-run")
            MainWindow.handle_content_snapshot(window, "迟到终态", "session", 1, "old-run")
            MainWindow.update_tool_card(window, {"id": "tool", "result": "旧结果"}, "session", 1, "old-run")
            self.assertEqual(state.current_content_buffer, "新回答")
            state.tool_cards["tool"].set_result.assert_not_called()


if __name__ == "__main__":
    unittest.main()
