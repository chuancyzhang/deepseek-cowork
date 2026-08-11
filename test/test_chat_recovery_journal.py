import json
import os
import tempfile
import unittest

from core.chat_recovery_journal import ChatRecoveryJournal
from core.chat_save_queue import ChatSaveRequest
from core.chat_storage import ChatStorage


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
            self.assertTrue(os.path.exists(path))

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
            self.assertEqual(storage.get_messages("session-v2"), storage.normalize_messages(messages))
            manifest = journal.runtime_journal.load_manifest("session-v2")
            self.assertEqual(manifest.get("pending_commit_run_id"), "")
            self.assertNotIn("pending_commit", manifest)
            self.assertEqual(journal.recover_into(storage), ([], []))


if __name__ == "__main__":
    unittest.main()
