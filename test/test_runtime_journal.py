import json
import os
import tempfile
import unittest

from core.runtime_journal import RuntimeJournal, RuntimeJournalError


class TestRuntimeJournal(unittest.TestCase):
    def test_run_events_are_checksummed_monotonic_and_replayable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            journal.begin_run(
                "session-1",
                "run-1",
                turn_id="turn-1",
                writer_owner="ui:1",
                base_messages=[{"id": "u1", "role": "user", "content": "hello"}],
            )

            first = journal.append_event("session-1", "run-1", "content", {"delta": "a"})
            second = journal.append_event("session-1", "run-1", "content", {"delta": "b"})

            self.assertEqual((first["sequence"], second["sequence"]), (1, 2))
            replay = journal.read_events("session-1", "run-1", starting_after=1)
            self.assertEqual([item["payload"]["delta"] for item in replay], ["b"])
            run = journal.get_run("session-1", "run-1")
            self.assertEqual(run["last_event_sequence"], 2)

    def test_corrupt_event_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            journal.begin_run("session-1", "run-1", writer_owner="ui:1")
            journal.append_event("session-1", "run-1", "content", {"delta": "a"})
            event_dir = os.path.join(
                journal.root,
                "sessions",
                journal._session_key("session-1"),
                "events",
            )
            path = os.path.join(event_dir, os.listdir(event_dir)[0])
            with open(path, "r", encoding="utf-8") as handle:
                envelope = json.loads(handle.readline())
            envelope["payload"]["payload"]["delta"] = "tampered"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(envelope) + "\n")

            with self.assertRaises(RuntimeJournalError):
                journal.read_events("session-1", "run-1")

    def test_active_run_blocks_parallel_run_and_writer_transfer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            journal.begin_run("session-1", "run-1", writer_owner="ui:1")

            with self.assertRaises(RuntimeJournalError):
                journal.begin_run("session-1", "run-2", writer_owner="daemon:2")

            journal.update_run("session-1", "run-1", {"status": "completed"})
            run = journal.begin_run("session-1", "run-2", writer_owner="daemon:2")
            self.assertEqual(run["writer_owner"], "daemon:2")

    def test_pending_commit_blocks_writer_transfer_until_acknowledged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            journal.begin_run("session-1", "run-1", writer_owner="ui:1")
            journal.update_run("session-1", "run-1", {"status": "completed"})
            messages = [{"id": "u1", "role": "user", "content": "hello"}]
            journal.mark_pending_commit("session-1", "run-1", messages)

            with self.assertRaises(RuntimeJournalError):
                journal.begin_run("session-1", "run-2", writer_owner="daemon:2")

            journal.acknowledge_commit("session-1", "run-1", messages)
            run = journal.begin_run("session-1", "run-2", writer_owner="daemon:2")
            self.assertEqual(run["writer_owner"], "daemon:2")

    def test_pending_commit_ack_requires_matching_sqlite_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            committed = [{"id": "u1", "role": "user", "content": "one"}]
            pending = committed + [
                {"id": "a1", "role": "assistant", "content": "two", "reasoning": "r"}
            ]
            journal.begin_run(
                "session-1",
                "run-1",
                writer_owner="ui:1",
                base_messages=committed,
            )
            journal.update_run("session-1", "run-1", {"status": "completed"})
            journal.mark_pending_commit("session-1", "run-1", pending)

            self.assertFalse(journal.acknowledge_commit("session-1", "run-1", committed))
            self.assertEqual(
                journal.load_manifest("session-1")["pending_commit_run_id"],
                "run-1",
            )
            sqlite_equivalent = [
                {**committed[0], "created_at": 1},
                {
                    "id": "a1",
                    "role": "assistant",
                    "content": "two",
                    "reasoning": "r",
                    "reasoning_content": "r",
                    "created_at": 2,
                },
            ]
            self.assertTrue(
                journal.acknowledge_commit("session-1", "run-1", sqlite_equivalent)
            )
            self.assertFalse(
                journal.load_manifest("session-1")["pending_commit_run_id"]
            )

    def test_tool_result_is_available_before_chat_commit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            journal.record_tool(
                "session-1",
                "execution-1",
                {
                    "name": "read_file",
                    "args_hash": "hash-1",
                    "status": "succeeded",
                    "result_obj": {"content": "saved"},
                },
            )

            reused = journal.find_tool_execution(
                "session-1",
                name="read_file",
                args_hash="hash-1",
                statuses={"succeeded"},
            )

            self.assertEqual(reused["execution_id"], "execution-1")
            self.assertEqual(reused["result_obj"], {"content": "saved"})


if __name__ == "__main__":
    unittest.main()
