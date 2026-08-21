import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core.runtime_journal import RuntimeJournal, RuntimeJournalError


class TestRuntimeJournal(unittest.TestCase):
    def test_atomic_write_retries_permission_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            target = os.path.join(temp_dir, "retry.json")
            real_replace = os.replace
            attempts = 0

            def replace_after_two_failures(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError("temporarily denied")
                return real_replace(source, destination)

            with (
                patch("core.runtime_journal.os.replace", side_effect=replace_after_two_failures),
                patch("core.runtime_journal.time.sleep"),
            ):
                journal._atomic_write(target, {"ok": True})

            self.assertEqual(attempts, 3)
            self.assertEqual(journal._read(target), {"ok": True})

    def test_atomic_write_raises_last_permission_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            target = os.path.join(temp_dir, "denied.json")
            denied = PermissionError("still denied")

            with (
                patch("core.runtime_journal.os.replace", side_effect=denied) as replace,
                patch("core.runtime_journal.time.sleep"),
            ):
                with self.assertRaises(PermissionError) as raised:
                    journal._atomic_write(target, {"ok": False})

            self.assertIs(raised.exception, denied)
            self.assertEqual(replace.call_count, 3)

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

    def test_pending_commit_uses_append_protocol_and_ignores_ui_projection_meta(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            base = [{
                "id": "u1",
                "role": "user",
                "content": "one",
                "meta": {"sequence": 0, "ui_turn_id": "1"},
            }]
            journal.begin_run(
                "session-append",
                "run-append",
                writer_owner="ui:1",
                base_messages=base,
            )
            full = [
                {**base[0], "meta": {"sequence": 0}},
                {"id": "a1", "role": "assistant", "content": "two"},
            ]
            journal.mark_pending_commit("session-append", "run-append", full)

            pending = journal.load_manifest("session-append")["pending_commit"]
            self.assertEqual(pending["format"], "ledger_append_v1")
            self.assertEqual([item["id"] for item in pending["append_messages"]], ["a1"])
            self.assertNotIn("messages", pending)
            self.assertTrue(journal.acknowledge_commit("session-append", "run-append", full))

    def test_stable_hash_ignores_ui_metadata_but_not_semantic_metadata(self):
        base = [{
            "id": "a1",
            "role": "assistant",
            "content": "answer",
            "meta": {"sequence": 1, "ui_turn_id": "3", "request_id": "run-1"},
        }]
        different_projection = [{
            "id": "a1",
            "role": "assistant",
            "content": "answer",
            "meta": {"sequence": 99, "ui_stage_id": "stage-4", "request_id": "run-1"},
            "created_at": 123,
        }]
        different_semantics = [{
            "id": "a1",
            "role": "assistant",
            "content": "answer",
            "meta": {"request_id": "run-2"},
        }]

        self.assertEqual(
            RuntimeJournal.messages_hash(base),
            RuntimeJournal.messages_hash(different_projection),
        )
        self.assertNotEqual(
            RuntimeJournal.messages_hash(base),
            RuntimeJournal.messages_hash(different_semantics),
        )

    def test_user_interruption_rejects_late_pending_commit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            base = [{"id": "u1", "role": "user", "content": "one"}]
            journal.begin_run(
                "session-stop",
                "run-stop",
                writer_owner="ui:1",
                base_messages=base,
            )
            journal.interrupt_run("session-stop", "run-stop")
            journal.update_run(
                "session-stop",
                "run-stop",
                {"status": "completed", "terminal_error": "late success"},
            )
            stopped_run = journal.get_run("session-stop", "run-stop")
            self.assertEqual(stopped_run["status"], "interrupted")
            self.assertEqual(stopped_run["terminal_error"], "interrupted by user")

            with self.assertRaises(RuntimeJournalError):
                journal.mark_pending_commit(
                    "session-stop",
                    "run-stop",
                    base + [{"id": "a1", "role": "assistant", "content": "late"}],
                )

    def test_completed_terminal_cannot_be_reclassified_or_interrupted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            journal.begin_run("session-terminal", "run-terminal", writer_owner="ui:1")
            journal.update_run("session-terminal", "run-terminal", {"status": "finalizing"})
            journal.update_run("session-terminal", "run-terminal", {"status": "completed"})

            with self.assertRaisesRegex(RuntimeJournalError, "cannot be replaced"):
                journal.update_run(
                    "session-terminal",
                    "run-terminal",
                    {"status": "interrupted", "terminal_error": "late reaper"},
                )
            preserved = journal.interrupt_run(
                "session-terminal",
                "run-terminal",
                reason="late user stop",
            )
            self.assertEqual(preserved["status"], "completed")
            self.assertFalse(preserved.get("stop_requested"))

    def test_finalizing_cannot_regress_to_running(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            journal.begin_run("session-finalizing", "run-finalizing", writer_owner="ui:1")
            journal.update_run(
                "session-finalizing",
                "run-finalizing",
                {"status": "finalizing"},
            )
            with self.assertRaisesRegex(RuntimeJournalError, "cannot regress"):
                journal.update_run(
                    "session-finalizing",
                    "run-finalizing",
                    {"status": "running"},
                )

    def test_list_runs_returns_newest_first(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            journal.begin_run("session-list", "run-old", writer_owner="ui:1")
            journal.update_run("session-list", "run-old", {"status": "failed"})
            journal.begin_run("session-list", "run-new", writer_owner="ui:1")

            runs = journal.list_runs("session-list")

            self.assertEqual([run["run_id"] for run in runs], ["run-new", "run-old"])

    def test_quarantine_pending_commit_removes_it_from_recovery_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            messages = [{"id": "u1", "role": "user", "content": "old"}]
            journal.begin_run(
                "session-quarantine",
                "run-quarantine",
                writer_owner="ui:old",
                base_messages=[],
                status="completed",
            )
            journal.mark_pending_commit(
                "session-quarantine",
                "run-quarantine",
                messages,
            )

            record = journal.quarantine_pending_commit(
                "session-quarantine",
                "run-quarantine",
                reason="history rewrite committed",
                committed_messages=[{"id": "u2", "role": "user", "content": "new"}],
            )

            self.assertEqual(record["run_id"], "run-quarantine")
            manifest = journal.load_manifest("session-quarantine")
            self.assertFalse(manifest.get("pending_commit_run_id"))
            self.assertNotIn("pending_commit", manifest)
            self.assertEqual(
                manifest["last_quarantined_commit"]["reason"],
                "history rewrite committed",
            )

    def test_quarantine_manifest_file_removes_malformed_manifest_from_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            journal = RuntimeJournal(temp_dir)
            session_dir = os.path.join(journal.root, "sessions", "legacy-broken")
            os.makedirs(session_dir, exist_ok=True)
            manifest_path = os.path.join(session_dir, "manifest.json")
            journal._atomic_write(
                manifest_path,
                {"pending_commit": {"run_id": "legacy-run"}},
            )

            manifests, errors = journal.scan_manifests()
            self.assertEqual(errors, [])
            self.assertEqual(manifests[0]["_runtime_manifest_path"], manifest_path)

            quarantined_path = journal.quarantine_manifest_file(
                manifest_path,
                reason="missing distributed session id",
            )

            self.assertFalse(os.path.exists(manifest_path))
            self.assertTrue(os.path.isfile(quarantined_path))
            self.assertTrue(os.path.isfile(f"{quarantined_path}.meta.json"))
            self.assertEqual(journal.scan_manifests(), ([], []))


if __name__ == "__main__":
    unittest.main()
