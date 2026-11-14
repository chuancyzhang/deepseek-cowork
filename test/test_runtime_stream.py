import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from core.runtime_journal import RuntimeJournal
from core.runtime_stream import RuntimeStream
from core.runtime_checkpoint import CheckpointRequest, RuntimeCheckpointWorker


class RuntimeStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cowork-stream-")
        self.addCleanup(self.temp.cleanup)
        self.journal = RuntimeJournal(self.temp.name)
        self.journal.begin_run("session", "run", writer_owner="test")

    def stream(self, write, failed=None, detach=None):
        stream = RuntimeStream(
            self.journal, "session", "run", write=write,
            detach=detach or (lambda reason: None),
            failed=failed or (lambda error: None), report=lambda **metrics: None,
        )
        self.addCleanup(stream.close)
        return stream

    def test_batch_preserves_events_sequences_and_one_fsync(self):
        entries = [{"type": "content", "payload": {"delta": str(i)}} for i in range(64)]
        with patch("core.runtime_journal.os.fsync", wraps=os.fsync) as sync:
            events = self.journal.append_events("session", "run", entries)
        self.assertEqual(sync.call_count, 1)
        self.assertEqual([event["sequence"] for event in events], list(range(1, 65)))
        self.assertEqual(self.journal.read_events("session", "run"), events)
        self.assertEqual(self.journal.append_event("session", "run", "final")["sequence"], 65)

    def test_stream_persists_before_publish_and_freezes_payload(self):
        sent, failures = [], []

        def write(payload, sequence):
            durable = self.journal.read_events("session", "run")
            self.assertEqual(durable[sequence - 1]["payload"], payload)
            self.assertEqual(durable[sequence - 1]["created_at"], payload["created_at"])
            sent.append((payload, sequence))
            return True

        stream = self.stream(write, failed=failures.append)
        for i in range(160):
            payload = {"type": "content", "delta": str(i), "data": {"source": i}}
            stream.enqueue(payload)
            self.assertNotIn("created_at", payload)
            payload["data"]["source"] = -1
        stream.enqueue({"type": "tool_call", "data": {"id": "tool-1"}})
        stream.enqueue({"type": "final", "result": {"content": "done"}}, terminal=True)
        stream.close()
        self.assertEqual(failures, [])
        self.assertEqual(len(sent), 162)
        self.assertEqual([p["data"]["source"] for p, _ in sent[:160]], list(range(160)))
        self.assertEqual([sequence for _, sequence in sent], list(range(1, 163)))

    def test_disconnected_subscriber_does_not_stop_persistence(self):
        detached = []
        stream = self.stream(lambda payload, sequence: False, detach=detached.append)
        for i in range(100):
            stream.enqueue({"type": "content", "delta": str(i)})
        stream.enqueue({"type": "final"}, terminal=True)
        stream.close()
        self.assertEqual(len(detached), 1)
        self.assertEqual(len(self.journal.read_events("session", "run")), 101)

    def test_fsync_failure_never_publishes_or_retries_batch(self):
        sent, failures = [], []
        stream = self.stream(lambda payload, sequence: sent.append(payload) or True, failed=failures.append)
        with patch("core.runtime_journal.os.fsync", side_effect=OSError("disk failure")) as sync:
            stream.enqueue({"type": "content", "delta": "retained"})
            with self.assertRaises(RuntimeError):
                stream.flush()
            stream.close()
            self.assertEqual(sync.call_count, 1)
        self.assertEqual(sent, [])
        self.assertEqual(len(failures), 1)
        self.assertEqual(stream.last_sequence, 0)

    def test_stop_releases_full_queue_and_drains_accepted_events(self):
        entered, release, producer_done = threading.Event(), threading.Event(), threading.Event()
        original = self.journal.append_events

        def slow_append(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test writer was not released")
            return original(*args)

        accepted = []
        with patch.object(self.journal, "append_events", side_effect=slow_append), patch.object(RuntimeStream, "MAX_EVENTS", 1):
            stream = self.stream(lambda payload, sequence: True)
            stream.enqueue({"type": "content", "delta": "first"})
            self.assertTrue(entered.wait(2))
            stream.enqueue({"type": "content", "delta": "second"})

            def produce():
                accepted.append(stream.enqueue({"type": "content", "delta": "third"}))
                producer_done.set()

            producer = threading.Thread(target=produce)
            producer.start()
            try:
                stream.stop_accepting()
                self.assertTrue(producer_done.wait(1))
                self.assertEqual(accepted, [False])
            finally:
                release.set()
                producer.join(3)
                stream.close()
        self.assertEqual([e["payload"]["delta"] for e in self.journal.read_events("session", "run")], ["first", "second"])

    def test_checkpoint_coalesces_periodic_but_keeps_boundary(self):
        worker = RuntimeCheckpointWorker(self.journal)
        for revision in range(1, 101):
            worker.enqueue(CheckpointRequest("session", "run", "instance", revision, str(revision), ""))
        worker.enqueue(CheckpointRequest("session", "run", "instance", 101, "final draft", "", "complete"))
        self.assertFalse(worker.enqueue(CheckpointRequest("session", "run", "instance", 102, "late", "")))
        worker.request_stop()
        worker.start()
        self.assertTrue(worker.join(5))
        self.assertEqual(self.journal.get_run("session", "run")["draft_content"], "final draft")
        self.assertEqual(len(self.journal.read_events("session", "run")), 2)

    def test_checkpoint_cannot_recreate_or_regress_terminal_run(self):
        args = dict(content="late", reasoning="", instance_id="instance", revision=1)
        self.assertEqual(self.journal.write_checkpoint("session", "missing", **args), "expired")
        self.journal.update_run("session", "run", {"status": "finalizing", "draft_content": "kept"})
        self.assertEqual(self.journal.write_checkpoint("session", "run", **args), "expired")
        self.journal.interrupt_run("session", "run")
        self.assertEqual(self.journal.write_checkpoint("session", "run", **args), "expired")
        self.assertEqual(self.journal.get_run("session", "run")["draft_content"], "kept")

    def test_stop_snapshot_is_saved_before_checkpoint_worker_exits(self):
        worker = RuntimeCheckpointWorker(self.journal)
        worker.enqueue(CheckpointRequest("session", "run", "instance", 1, "old", ""))
        worker.enqueue(CheckpointRequest("session", "run", "instance", 2, "visible answer", "thought", "stop"))
        worker.request_stop()
        worker.start()
        self.assertTrue(worker.join(5))
        run = self.journal.get_run("session", "run")
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["draft_content"], "visible answer")
        self.assertEqual(run["draft_reasoning"], "thought")


if __name__ == "__main__":
    unittest.main()
