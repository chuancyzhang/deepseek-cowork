"""Fault boundaries for shared durable facts and their SQLite projection."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from core.desktop_store import DesktopChatStorage, DesktopStore
from core.runtime_journal import RuntimeJournalError
from core.runtime_ledger import RuntimeLedger, LedgerJournal
from core.conversation_integrity import canonical_ledger_messages_hash


class RuntimeLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.storage = DesktopChatStorage(str(self.root / "chat_history.sqlite"))
        self.store = DesktopStore(self.storage)
        self.storage.upsert_conversation("chat", title="新对话")
        self.ledgers = []
        self.addCleanup(self.close_ledgers)

    def close_ledgers(self):
        for ledger in self.ledgers:
            try:
                ledger.close()
            except RuntimeJournalError:
                pass

    def open(self, cls=RuntimeLedger):
        ledger = cls(self.storage)
        self.ledgers.append(ledger)
        self.storage.runtime_ledger = ledger
        self.journal = self.storage.runtime_journal = LedgerJournal(ledger)
        return ledger

    def submit(self):
        with self.journal.session_lock("chat"):
            run, created = self.store.accept({"conversation_id": "chat", "request_id": "run", "message_id": "user",
                "content": "保留输入", "display_text": "保留输入"})
            if created:
                self.journal.begin_run("chat", "run", writer_owner="desktop:test", base_messages=[])
                self.journal.append_event("chat", "run", "accepted", {"message_id": "user"})
        return run, created

    def test_original_journal_reads_lazily_and_continues_without_reimport(self):
        from core.runtime_journal import RuntimeJournal
        legacy = RuntimeJournal(self.root)
        legacy.begin_run("chat", "old-run", writer_owner="old", base_messages=[])
        legacy.record_attempt("chat", "attempt", {"run_id": "old-run", "text": "原请求"})
        legacy.append_event("chat", "old-run", "content", {"text": "已保存片段"})
        original = {str(path): path.read_bytes() for path in (self.root / "runtime_journal_v2").rglob("*") if path.is_file()}
        ledger = self.open()
        self.assertEqual(ledger.records("sessions/"), {})
        self.assertEqual(self.journal.get_attempt("chat", "attempt")["text"], "原请求")
        self.assertEqual(self.journal.list_runs("chat")[0]["run_id"], "old-run")
        self.assertEqual(self.journal.scan_manifests()[0][0]["session_id"], "chat")
        self.assertEqual(self.journal.read_events("chat", "old-run")[0]["payload"]["text"], "已保存片段")
        self.journal.append_event("chat", "old-run", "content", {"text": "新增片段"})
        self.journal.update_run("chat", "old-run", {"status": "interrupted"})
        self.assertEqual(self.journal.get_run("chat", "old-run")["status"], "interrupted")
        self.assertEqual([e["sequence"] for e in self.journal.read_events("chat", "old-run")], [1, 2])
        self.assertEqual({name: Path(name).read_bytes() for name in original}, original)
        ledger.close(); self.ledgers.remove(ledger)
        self.open()
        self.assertEqual(self.journal.get_run("chat", "old-run")["status"], "interrupted")
        self.assertEqual([e["sequence"] for e in self.journal.read_events("chat", "old-run")], [1, 2])

    def test_corrupt_original_record_is_reported_without_substitution(self):
        from core.runtime_journal import RuntimeJournal
        legacy = RuntimeJournal(self.root)
        legacy.begin_run("chat", "old-run", writer_owner="old", base_messages=[])
        Path(legacy._record_path("chat", "runs", "old-run")).write_text("{}", encoding="utf-8")
        self.open()
        with self.assertRaises(RuntimeJournalError):
            self.journal.get_run("chat", "old-run")

    def test_receipt_waits_for_sync_but_not_sqlite_projection(self):
        entered, release = threading.Event(), threading.Event()
        class PausedLedger(RuntimeLedger):
            def _apply(inner, db, record):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test projection gate")
                super()._apply(db, record)
        ledger = self.open(PausedLedger)
        self.addCleanup(release.set)
        self.assertTrue(self.submit()[1])
        self.assertTrue(entered.wait(2))
        self.assertGreater(ledger.durable, ledger.projected)
        with closing(sqlite3.connect(self.storage.db_path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages").fetchone()[0], 0)
        release.set()
        ledger.barrier()
        self.assertEqual(len(self.storage.get_messages("chat")), 1)
        self.assertFalse(self.submit()[1])

    def test_projection_rollback_replays_whole_submission_once(self):
        class FailedLedger(RuntimeLedger):
            def _apply(inner, db, record):
                super()._apply(db, record)
                raise OSError("injected SQLite commit failure")
        failed = self.open(FailedLedger)
        self.submit()
        failed.projector.join(2)
        with self.assertRaises(RuntimeJournalError):
            failed.barrier()
        with self.assertRaises(RuntimeJournalError):
            failed.close()
        recovered = self.open()
        self.assertEqual(len(self.storage.get_messages("chat")), 1)
        self.assertEqual(self.store.get_run("run")["message_id"], "user")
        self.assertEqual(self.journal.read_events("chat", "run")[0]["type"], "accepted")
        self.assertEqual(recovered.projected, recovered.durable)
        self.assertFalse(self.submit()[1])

    def test_duplicate_concurrent_submission_has_one_message_and_run(self):
        ledger = self.open()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.submit(), range(8)))
        self.assertEqual(sum(created for _, created in results), 1)
        ledger.barrier()
        self.assertEqual([m["id"] for m in self.storage.get_messages("chat")], ["user"])
        self.assertEqual(len(self.store.active_runs()), 1)

    def test_history_rewrite_and_undo_survive_restart_without_resurrection(self):
        ledger = self.open()
        self.submit()
        before = self.storage.get_messages("chat")
        with self.journal.session_lock("chat"):
            self.store.put_state("history_undo:edit", before)
            self.storage.rewrite_conversation_safely("chat", [], expected_messages_hash=canonical_ledger_messages_hash(before), expected_revision=0)
        ledger.close()
        self.open()
        self.assertEqual(self.storage.get_messages("chat"), [])
        self.assertEqual(self.store.get_state("history_undo:edit"), before)
        with self.journal.session_lock("chat"):
            self.storage.rewrite_conversation_safely("chat", before, expected_messages_hash=canonical_ledger_messages_hash([]), expected_revision=1)
            self.store.put_state("history_undo:edit", None)
        self.storage.runtime_ledger.close()
        self.open()
        self.assertEqual([m["id"] for m in self.storage.get_messages("chat")], ["user"])
        self.assertIsNone(self.store.get_state("history_undo:edit"))

    def test_incomplete_tail_is_preserved_and_complete_prefix_recovers(self):
        ledger = self.open()
        self.submit()
        ledger.close()
        path = sorted(ledger.root.glob("*.jsonl"))[-1]
        with path.open("ab") as handle:
            handle.write(b'{"payload":')
        self.open()
        self.assertEqual(path.with_suffix(".incomplete").read_bytes(), b'{"payload":')
        self.assertEqual(len(self.storage.get_messages("chat")), 1)

    def test_corrupt_complete_record_stops_recovery(self):
        ledger = self.open()
        self.submit()
        ledger.close()
        path = sorted(ledger.root.glob("*.jsonl"))[-1]
        envelope = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        envelope["checksum"] = "bad"
        path.write_text(json.dumps(envelope) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeJournalError, "校验失败"):
            self.open()

    def test_segment_recycling_retains_full_event_history(self):
        ledger = self.open()
        ledger.SEGMENT_BYTES = 1
        self.submit()
        for index in range(6):
            self.journal.append_event("chat", "run", "tool_result", {"text": str(index)})
        ledger.close()
        self.assertLess(len(list(ledger.root.glob("*.jsonl"))), 7)
        self.open()
        events = self.journal.read_events("chat", "run")
        self.assertEqual([e["sequence"] for e in events], list(range(1, 8)))
        self.assertEqual(events[-1]["payload"], {"text": "5"})

    def test_failed_sync_never_advances_receipt(self):
        ledger = self.open()
        self.submit()
        ledger.barrier()
        previous = ledger.durable
        with patch("core.runtime_ledger.platform_runtime.sync_file", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.journal.append_event("chat", "run", "content", {"delta": "未确认"})
        self.assertEqual(ledger.durable, previous)

    def test_cancel_signals_worker_even_when_persistence_is_broken(self):
        from core.runtime_coordinator import RuntimeCoordinator
        state = RuntimeCoordinator.__new__(RuntimeCoordinator)
        state.lock = threading.RLock()
        worker = Mock()
        worker.request_id = "run"
        state.active_workers = {"chat": {"worker": worker, "run_id": "run"}}
        state.detached_workers, state.cancelled_runs = {}, set()
        state.runtime_journal = Mock()
        state.runtime_journal.session_lock.side_effect = OSError("disk full")
        state._close_live_subagents = Mock()
        result = state.stop_session("chat", "run", wait_for_release=False)
        self.assertTrue(result)
        worker.stop.assert_called_once()
        self.assertIn(("chat", "run"), state.cancelled_runs)

    def coordinator(self):
        from core.runtime_coordinator import RuntimeCoordinator
        state = RuntimeCoordinator.__new__(RuntimeCoordinator)
        state.chat_storage, state.desktop_store, state.runtime_journal = self.storage, self.store, self.journal
        state.lock = threading.RLock()
        state.sessions, state.active_workers, state.accepted_runs, state.detached_workers = {}, {}, {}, {}
        state.cancelled_runs = set()
        state.update_preparing = state.shutdown_requested = False
        state.daemon_instance_id = "fixture"
        state._close_live_subagents = Mock()
        return state

    def test_channel_input_is_durable_before_start_and_retries_claim_once(self):
        self.open()
        state = self.coordinator()
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda _: state.prepare_stream("chat", "run", "run", "daemon:test", "渠道输入", "user", None), range(8)))
        self.assertEqual(sum(item is not None for item in claims), 1)
        self.assertEqual([m["content"] for m in self.storage.get_messages("chat")], ["渠道输入"])
        self.assertEqual(len(self.journal.list_runs("chat")), 1)
        state.recover_runs()
        self.assertEqual(self.journal.get_run("chat", "run")["status"], "interrupted")
        self.assertIsNone(state.prepare_stream("chat", "run", "run", "daemon:test", "渠道输入", "user", None))

    def test_final_keeps_new_result_after_session_normalization_and_replays_it(self):
        self.open()
        state = self.coordinator()
        messages = state.prepare_stream("chat", "run", "run", "daemon:test", "输入", "user", None)
        state.finalize("chat", "run", "run", messages, {"content": "完整回复", "message_id": "answer"})
        self.assertEqual([m["content"] for m in self.storage.get_messages("chat")], ["输入", "完整回复"])
        received = []
        state.replay_stream("chat", "run", received.append)
        self.assertEqual(received[-1]["result"]["content"], "完整回复")

    def test_guidance_deduplicates_even_after_final_and_rejects_identity_reuse(self):
        self.open()
        state = self.coordinator()
        messages = state.prepare_stream("chat", "run", "run", "daemon:test", "输入", "user", None)
        worker = Mock()
        worker.steer.return_value = {"accepted": True, "turn_id": "run"}
        state.set_active_worker("chat", worker, "run", "run", "daemon:test")
        guidance = {"id": "guidance", "role": "user", "content": "补充"}
        first = state.steer_session("chat", "run", guidance)
        self.assertTrue(first["message_committed"])
        self.assertEqual(state.steer_session("chat", "run", guidance), first)
        state.finalize("chat", "run", "run", messages, {"content": "回答", "message_id": "answer"})
        self.assertEqual(state.steer_session("chat", "run", guidance), first)
        worker.steer.assert_called_once()
        self.assertEqual([m["id"] for m in self.storage.get_messages("chat")], ["user", "guidance", "answer"])
        with self.assertRaisesRegex(ValueError, "标识"):
            state.steer_session("chat", "run", {**guidance, "content": "不同内容"})

    def test_guidance_at_worker_end_stays_saved_with_explicit_delivery_result(self):
        self.open()
        state = self.coordinator()
        messages = state.prepare_stream("chat", "run", "run", "daemon:test", "输入", "user", None)
        worker = Mock()
        worker.steer.return_value = {"accepted": False, "error": "turn_not_active", "turn_id": "run"}
        state.set_active_worker("chat", worker, "run", "run", "daemon:test")
        response = state.steer_session("chat", "run", {"id": "late", "role": "user", "content": "结束边界补充"})
        self.assertFalse(response["accepted"])
        self.assertTrue(response["message_committed"])
        state.finalize("chat", "run", "run", messages, {"content": "回答", "message_id": "answer"})
        self.assertEqual([m["id"] for m in self.storage.get_messages("chat")], ["user", "late", "answer"])

    def test_stop_before_registration_blocks_worker_and_late_cleanup_is_fenced(self):
        self.open()
        state = self.coordinator()
        state.prepare_stream("chat", "run", "run", "daemon:test", "输入", "user", None)
        self.assertTrue(state.cancel_tokens("chat", "run"))
        self.assertFalse(state.set_active_worker("chat", Mock(), "run", "run", "daemon:test"))
        current, old = Mock(), Mock()
        state.active_workers["chat"] = {"worker": current, "run_id": "new"}
        self.assertFalse(state.clear_active_worker("chat", expected_worker=old, expected_run_id="run"))
        self.assertIs(state.active_workers["chat"]["worker"], current)

    def test_attach_during_preparation_does_not_interrupt_current_execution(self):
        self.open()
        state = self.coordinator()
        state.prepare_stream("chat", "run", "run", "daemon:test", "输入", "user", None)
        response = state.request({"action": "attach_run", "session_id": "chat", "run_id": "run"})
        self.assertEqual(response["run"]["status"], "running")


if __name__ == "__main__":
    unittest.main()

class SharedSubagentRuntimeTests(unittest.TestCase):
    def test_shutdown_drains_child_after_parent_is_no_longer_active(self):
        import os
        from unittest.mock import patch
        from core.desktop_service import DesktopService
        from core.agent_manager import get_agent_manager_registry
        from test_subagent_reliable_runtime import Worker
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'COWORK_APP_DATA_DIR': root, 'COWORK_EXTERNAL_BACKGROUND_SERVICES': '0'}):
            service = DesktopService(lambda event: None)
            workers = []
            def factory(*args):
                worker = Worker(*args)
                def stop():
                    worker.stopped = True
                    worker.complete('preserved child output')
                worker.stop = stop
                workers.append(worker)
                return worker
            manager = get_agent_manager_registry().get_session_manager('independent-parent', service.storage, service.config, worker_factory=factory)
            aid = manager.spawn_agent('child')['agent_id']
            workers[0].content_signal.emit('preserved child output')
            self.assertEqual(service.store.active_runs(), [])
            try:
                self.assertTrue(service.dispatch('app.shutdown', {})['stopped'])
                self.assertTrue(workers[0].stopped)
                self.assertEqual(service.storage.get_agent(aid)['status'], 'interrupted')
                self.assertIn('preserved child output', [m['content'] for m in service.storage.get_agent_messages(aid)])
                self.assertEqual(get_agent_manager_registry().active_count(service.storage.db_path), 0)
            finally:
                service.server.shutdown()
                service.server.server_close()
                service.state.skill_catalog.stop_watching()

    def test_child_output_queue_and_terminal_share_durable_pipeline(self):
        from types import SimpleNamespace
        from core.agent_manager import SessionAgentManager
        from test_subagent_reliable_runtime import Worker, Signal
        with tempfile.TemporaryDirectory() as root:
            storage = DesktopChatStorage(str(Path(root) / 'chat.sqlite'))
            ledger = RuntimeLedger(storage)
            storage.runtime_ledger = ledger
            journal = storage.runtime_journal = LedgerJournal(ledger)
            config = SimpleNamespace(runtime_journal=journal, get=lambda key, default=None: default,
                get_god_mode=lambda: False, get_chat_workspace_root=lambda: root)
            workers, events = [], []
            signal = Signal()
            signal.subscribe(events.append)
            def factory(*args):
                worker = Worker(*args)
                workers.append(worker)
                return worker
            manager = SessionAgentManager('parent', storage, config, worker_factory=factory, agent_state_signal=signal)
            try:
                aid = manager.spawn_agent('first')['agent_id']
                for _ in range(80):
                    workers[0].content_signal.emit('正文')
                manager.send_input(aid, 'second', 'u2')
                manager.send_input(aid, 'second', 'u2')
                self.assertEqual(len(storage.agent_inbox(aid)), 2)
                workers[0].complete('answer one')
                self.assertEqual([m['content'] for m in workers[1].messages], ['first', 'answer one', 'second'])
                workers[1].complete('answer two')
                ledger.barrier()
                self.assertEqual(storage.get_agent(aid)['status'], 'completed')
                self.assertEqual(len([e for e in events if e['status'] == 'content']), 80)
                self.assertEqual([m['content'] for m in storage.get_agent_messages(aid)], ['first', 'answer one', 'second', 'answer two'])
                self.assertLess(ledger.metrics['projection_transactions'], 80)
                with ledger.connection() as db:
                    saved = [json.loads(row[0]) for row in db.execute('SELECT value FROM runtime_events')]
                self.assertEqual(len([e for e in saved if e['payload'].get('status') == 'content']), 80)
            finally:
                for record in manager._agents.values():
                    if record.stream:
                        record.stream.close()
                ledger.close()
