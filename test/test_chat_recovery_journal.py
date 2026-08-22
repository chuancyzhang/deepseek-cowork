import json
import os
import tempfile
import unittest

from core.chat_recovery_journal import ChatRecoveryJournal
from core.chat_save_queue import ChatSaveRequest
from core.chat_storage import ChatStorage
from core.runtime_journal import RuntimeJournal


class TestChatRecoveryJournal(unittest.TestCase):
    def _request(self, revision, messages=None, status="running"):
        return ChatSaveRequest(
            session_id="session-recovery",
            messages=messages or [{"id": "m1", "role": "user", "content": "保留我"}],
            title="恢复会话",
            status=status,
            meta={"workspace_source": "chat"},
            ready_at=0.0,
            revision=revision,
        )

    def test_newer_revision_is_not_removed_by_older_acknowledgement(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = ChatRecoveryJournal(temp_dir)
            journal.record(self._request(1))
            journal.record(
                self._request(
                    2,
                    messages=[
                        {"id": "m1", "role": "user", "content": "保留我"},
                        {"id": "m2", "role": "assistant", "content": "最新回复"},
                    ],
                )
            )

            self.assertFalse(journal.acknowledge("session-recovery", 1))
            self.assertTrue(journal.acknowledge("session-recovery", 2))

    def test_recovery_replays_snapshot_once_and_marks_running_interrupted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            journal = ChatRecoveryJournal(temp_dir)
            journal.record(self._request(3))

            recovered, errors = journal.recover_into(storage)
            self.assertEqual(recovered, ["session-recovery"])
            self.assertEqual(errors, [])
            self.assertEqual(
                [item.get("content") for item in storage.get_messages("session-recovery")],
                ["保留我"],
            )
            self.assertEqual(
                (storage.get_conversation_record("session-recovery") or {}).get("status"),
                "interrupted",
            )

            recovered_again, errors_again = journal.recover_into(storage)
            self.assertEqual(recovered_again, [])
            self.assertEqual(errors_again, [])

    def test_recovery_reconciles_legacy_interrupted_projection_before_append(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            storage.save_conversation(
                "session-recovery",
                [
                    {"id": "u1", "role": "user", "content": "开始"},
                    {
                        "id": "legacy-interrupted",
                        "role": "assistant",
                        "content": "任务已停止",
                        "meta": {"ui_reply_kind": "interrupted"},
                    },
                ],
                title="旧会话",
            )
            journal = ChatRecoveryJournal(temp_dir)
            journal.record(
                self._request(
                    4,
                    messages=[
                        {"id": "u1", "role": "user", "content": "开始"},
                        {"id": "u2", "role": "user", "content": "总结下"},
                    ],
                )
            )

            recovered, errors = journal.recover_into(storage)

            self.assertEqual(recovered, ["session-recovery"])
            self.assertEqual(errors, [])
            self.assertEqual(
                [item.get("id") for item in storage.get_messages("session-recovery")],
                ["u1", "u2"],
            )
            self.assertFalse(os.path.exists(journal._path_for_session("session-recovery")))

    def test_corrupt_journal_is_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = ChatRecoveryJournal(temp_dir)
            path = os.path.join(journal.directory, "broken.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"journal_version": 1, "checksum": "bad", "payload": {}}, handle)
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))

            recovered, errors = journal.recover_into(storage)
            self.assertEqual(recovered, [])
            self.assertEqual(len(errors), 1)
            self.assertFalse(os.path.exists(path))
            self.assertEqual(errors[0]["source"], "pending_chat_save_quarantined")
            self.assertTrue(os.path.exists(errors[0]["quarantine_path"]))

    def test_recovery_remaps_message_id_owned_by_another_conversation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            storage.save_conversation(
                "existing-session",
                [{"id": "shared-id", "role": "assistant", "content": "existing"}],
                title="Existing",
            )
            journal = ChatRecoveryJournal(temp_dir)
            journal.record(
                self._request(
                    4,
                    messages=[
                        {"id": "shared-id", "role": "assistant", "content": "recovered"}
                    ],
                )
            )

            recovered, errors = journal.recover_into(storage)
            self.assertEqual(recovered, ["session-recovery"])
            self.assertEqual(errors, [])
            recovered_message = storage.get_messages("session-recovery")[0]
            self.assertNotEqual(recovered_message.get("id"), "shared-id")
            self.assertEqual(
                (recovered_message.get("meta") or {}).get("recovered_original_message_id"),
                "shared-id",
            )

    def test_stream_checkpoint_is_moved_to_v2_without_entering_sqlite_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            journal = ChatRecoveryJournal(temp_dir)
            journal.record(
                self._request(
                    5,
                    messages=[
                        {"id": "u1", "role": "user", "content": "继续执行"},
                        {
                            "id": "partial",
                            "role": "assistant",
                            "content": "已经完成前半部分",
                            "meta": {"recovery_checkpoint": True},
                        },
                    ],
                )
            )

            recovered, errors = journal.recover_into(storage)
            self.assertEqual(recovered, ["session-recovery"])
            self.assertEqual(errors, [])
            self.assertEqual(
                [item.get("content") for item in storage.get_messages("session-recovery")],
                ["继续执行"],
            )
            run = journal.runtime_journal.get_run("session-recovery", "partial")
            self.assertEqual(run["status"], "interrupted")
            self.assertEqual(run["draft_content"], "已经完成前半部分")

    def test_v2_pending_commit_is_recovered_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            journal = ChatRecoveryJournal(temp_dir)
            messages = [
                {"id": "u1", "role": "user", "content": "question"},
                {"id": "a1", "role": "assistant", "content": "answer"},
            ]
            journal.runtime_journal.begin_run(
                "session-v2",
                "run-v2",
                writer_owner="ui:1",
                base_messages=[],
                status="completed",
                extra={"finished_at": 1},
            )
            journal.runtime_journal.mark_pending_commit(
                "session-v2",
                "run-v2",
                messages,
                title="Recovered v2",
            )

            recovered, errors = journal.recover_into(storage)

            self.assertEqual(recovered, ["session-v2"])
            self.assertEqual(errors, [])
            recovered_messages = storage.get_messages("session-v2")
            self.assertEqual(
                RuntimeJournal.messages_hash(recovered_messages),
                RuntimeJournal.messages_hash(storage.normalize_messages(messages)),
            )
            self.assertTrue(all(item.get("created_at") for item in recovered_messages))
            manifest = journal.runtime_journal.load_manifest("session-v2")
            self.assertEqual(manifest.get("pending_commit_run_id"), "")
            self.assertNotIn("pending_commit", manifest)
            self.assertEqual(journal.recover_into(storage), ([], []))

    def test_distributed_legacy_v2_snapshot_upgrades_once_and_preserves_meta(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            storage.save_conversation(
                "session-legacy-v2",
                [{
                    "id": "u1",
                    "role": "user",
                    "content": "question",
                    "meta": {"sequence": 0, "ui_turn_id": "1"},
                }],
                title="Legacy",
                meta={"ui_timeline_v1": [{"kind": "thinking", "turn_id": "1"}]},
            )
            journal = ChatRecoveryJournal(temp_dir)
            messages = [
                {"id": "u1", "role": "user", "content": "question", "meta": {"sequence": 0}},
                {"id": "a1", "role": "assistant", "content": "answer"},
            ]
            journal.runtime_journal.begin_run(
                "session-legacy-v2",
                "run-legacy-v2",
                writer_owner="ui:old",
                base_messages=[],
                status="completed",
            )
            journal.runtime_journal.update_manifest(
                "session-legacy-v2",
                {
                    "pending_commit_run_id": "run-legacy-v2",
                    "pending_commit": {
                        "run_id": "run-legacy-v2",
                        "messages": messages,
                        "messages_hash": RuntimeJournal.legacy_snapshot_messages_hash(messages),
                        "title": "Legacy",
                        "status": "completed",
                        "meta": {},
                    },
                },
            )

            recovered, errors = journal.recover_into(storage)

            self.assertEqual(recovered, ["session-legacy-v2"])
            self.assertEqual(errors, [])
            self.assertEqual([item["id"] for item in storage.get_messages("session-legacy-v2")], ["u1", "a1"])
            self.assertIn("ui_timeline_v1", storage.get_conversation_meta("session-legacy-v2"))

    def test_distributed_legacy_snapshot_is_acknowledged_when_sqlite_is_newer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            old_messages = [{"id": "u1", "role": "user", "content": "old"}]
            storage.save_conversation(
                "session-legacy-stale",
                old_messages + [{"id": "a2", "role": "assistant", "content": "newer"}],
                title="Newer",
            )
            journal = ChatRecoveryJournal(temp_dir)
            journal.runtime_journal.begin_run(
                "session-legacy-stale",
                "run-legacy-stale",
                writer_owner="ui:old",
                base_messages=[],
                status="completed",
            )
            journal.runtime_journal.update_manifest(
                "session-legacy-stale",
                {
                    "pending_commit_run_id": "run-legacy-stale",
                    "pending_commit": {
                        "run_id": "run-legacy-stale",
                        "messages": old_messages,
                        "messages_hash": RuntimeJournal.legacy_snapshot_messages_hash(old_messages),
                        "title": "Old",
                        "status": "completed",
                    },
                },
            )

            recovered, errors = journal.recover_into(storage)

            self.assertEqual(recovered, ["session-legacy-stale"])
            self.assertEqual(errors, [])
            self.assertEqual(
                [message["id"] for message in storage.get_messages("session-legacy-stale")],
                ["u1", "a2"],
            )
            self.assertEqual(
                journal.runtime_journal.load_manifest("session-legacy-stale")["pending_commit_run_id"],
                "",
            )

    def test_distributed_legacy_snapshot_is_quarantined_after_history_rewrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            current_messages = [{"id": "u-new", "role": "user", "content": "new branch"}]
            storage.save_conversation(
                "session-legacy-diverged",
                current_messages,
                title="Current",
            )
            journal = ChatRecoveryJournal(temp_dir)
            old_messages = [{"id": "u-old", "role": "user", "content": "old branch"}]
            journal.runtime_journal.begin_run(
                "session-legacy-diverged",
                "run-legacy-diverged",
                writer_owner="ui:old",
                base_messages=[],
                status="completed",
            )
            journal.runtime_journal.update_manifest(
                "session-legacy-diverged",
                {
                    "pending_commit_run_id": "run-legacy-diverged",
                    "pending_commit": {
                        "run_id": "run-legacy-diverged",
                        "messages": old_messages,
                        "messages_hash": RuntimeJournal.legacy_snapshot_messages_hash(
                            old_messages
                        ),
                        "title": "Old",
                        "status": "completed",
                    },
                },
            )

            recovered, errors = journal.recover_into(storage)

            self.assertEqual(recovered, [])
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0]["source"], "runtime_pending_commit_quarantined")
            stored_messages = storage.get_messages("session-legacy-diverged")
            self.assertEqual(
                RuntimeJournal.messages_hash(stored_messages),
                RuntimeJournal.messages_hash(storage.normalize_messages(current_messages)),
            )
            self.assertTrue(all(item.get("created_at") for item in stored_messages))
            manifest = journal.runtime_journal.load_manifest("session-legacy-diverged")
            self.assertFalse(manifest.get("pending_commit_run_id"))
            self.assertNotIn("pending_commit", manifest)
            self.assertEqual(journal.recover_into(storage), ([], []))

    def test_malformed_distributed_manifest_is_quarantined_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            journal = ChatRecoveryJournal(temp_dir)
            session_dir = os.path.join(
                journal.runtime_journal.root,
                "sessions",
                "legacy-missing-session-id",
            )
            os.makedirs(session_dir, exist_ok=True)
            manifest_path = os.path.join(session_dir, "manifest.json")
            legacy_messages = [
                {"id": "u-old", "role": "user", "content": "legacy branch"}
            ]
            journal.runtime_journal._atomic_write(
                manifest_path,
                {
                    "pending_commit_run_id": "run-legacy",
                    "pending_commit": {
                        "run_id": "run-legacy",
                        "messages": legacy_messages,
                        "messages_hash": RuntimeJournal.legacy_snapshot_messages_hash(
                            legacy_messages
                        ),
                    },
                },
            )

            recovered, errors = journal.recover_into(storage)

            self.assertEqual(recovered, [])
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0]["source"], "runtime_pending_commit_quarantined")
            self.assertTrue(os.path.isfile(errors[0]["quarantine_path"]))
            self.assertFalse(os.path.exists(manifest_path))
            self.assertEqual(journal.recover_into(storage), ([], []))

    def test_periodic_recovery_skips_live_session_without_consuming_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            journal = ChatRecoveryJournal(temp_dir)
            messages = [{"id": "u1", "role": "user", "content": "question"}]
            journal.runtime_journal.begin_run(
                "session-live",
                "run-live",
                writer_owner="ui:1",
                base_messages=[],
            )
            journal.runtime_journal.mark_pending_commit(
                "session-live",
                "run-live",
                messages,
            )

            recovered, errors = journal.recover_into(
                storage,
                skip_session_ids={"session-live"},
            )

            self.assertEqual((recovered, errors), ([], []))
            self.assertEqual(storage.get_messages("session-live"), [])
            self.assertEqual(
                journal.runtime_journal.load_manifest("session-live")["pending_commit_run_id"],
                "run-live",
            )

    def test_ui_snapshot_recovers_before_runtime_append(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            base = [{"id": "u0", "role": "user", "content": "old"}]
            storage.save_conversation("session-order", base, title="Order")
            journal = ChatRecoveryJournal(temp_dir)
            ui_user = {
                "id": "u1",
                "role": "user",
                "content": "new",
                "meta": {"turn_id": "2", "ui_turn_id": "2"},
            }
            journal.record(
                ChatSaveRequest(
                    session_id="session-order",
                    messages=base + [ui_user],
                    title="Order",
                    status="running",
                    meta={"ui_timeline_v1": [{"kind": "thinking", "turn_id": "2"}]},
                    ready_at=0.0,
                    revision=1,
                )
            )
            journal.runtime_journal.begin_run(
                "session-order",
                "run-order",
                writer_owner="ui:1",
                base_messages=base,
            )
            daemon_user = {
                "id": "u1",
                "role": "user",
                "content": "new",
                "meta": {"turn_id": "2"},
            }
            assistant = {
                "id": "a1",
                "role": "assistant",
                "content": "answer",
                "meta": {"turn_id": "2"},
            }
            journal.runtime_journal.mark_pending_commit(
                "session-order",
                "run-order",
                base + [daemon_user, assistant],
            )

            recovered, errors = journal.recover_into(storage)

            self.assertEqual(recovered, ["session-order"])
            self.assertEqual(errors, [])
            self.assertEqual(
                [message["id"] for message in storage.get_messages("session-order")],
                ["u0", "u1", "a1"],
            )
            self.assertIn("ui_timeline_v1", storage.get_conversation_meta("session-order"))


if __name__ == "__main__":
    unittest.main()
