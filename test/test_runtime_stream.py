import os
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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
            sent.append((payload, sequence))
            return True

        stream = self.stream(write, failed=failures.append)
        for i in range(160):
            payload = {"type": "content", "delta": str(i), "data": {"source": i}}
            stream.enqueue(payload)
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
        self.assertTrue(worker.wait(5000))
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
        self.assertTrue(worker.wait(5000))
        run = self.journal.get_run("session", "run")
        self.assertEqual(run["status"], "interrupted")
        self.assertEqual(run["draft_content"], "visible answer")
        self.assertEqual(run["draft_reasoning"], "thought")


class HiddenBodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_hidden_body_renders_latest_once_when_shown(self):
        import main
        bubble = main.ChatBubble("Agent", "", thinking="...")
        self.addCleanup(bubble.deleteLater)
        with patch("main.render_markdown_or_html_with_cache", wraps=main.render_markdown_or_html_with_cache) as render:
            for i in range(100):
                bubble.set_main_content(f"**answer {i}**")
            bubble.set_main_content("**final answer**", final=True)
            self.app.processEvents()
            self.assertEqual(render.call_count, 0)
            self.assertTrue(bubble._pending_main_content_final)
            bubble.show()
            for _ in range(4):
                self.app.processEvents()
            self.assertEqual(render.call_count, 1)
            self.assertEqual(bubble.content_edit.toPlainText().strip(), "final answer")
            self.assertTrue(bubble._rendered_main_content_final)
        bubble.hide()

    def test_plain_append_preserves_user_selection(self):
        import main
        from PySide6.QtGui import QTextCursor
        bubble = main.ChatBubble("Agent", "", thinking="...")
        self.addCleanup(bubble.deleteLater)
        bubble.show()
        text = "abcdefghij " * 250
        bubble.set_main_content(text)
        bubble._flush_pending_main_content_render()
        cursor = bubble.content_edit.textCursor()
        cursor.setPosition(1)
        cursor.setPosition(7, QTextCursor.KeepAnchor)
        bubble.content_edit.setTextCursor(cursor)
        selection = cursor.selectedText()
        bubble.set_main_content(text + "tail")
        bubble._flush_pending_main_content_render()
        self.assertEqual(bubble.content_edit.textCursor().selectedText(), selection)
        self.assertTrue(bubble.content_edit.toPlainText().endswith("tail"))
        bubble.hide()

    def test_hidden_result_completes_process_without_rendering(self):
        import main
        from PySide6.QtWidgets import QWidget
        host = QWidget()
        self.addCleanup(host.deleteLater)
        group = main.AssistantTurnGroup("hidden-result", parent=host)
        bubble = main.ChatBubble("Agent", "", thinking="...")
        group.add_stage(bubble)
        self.assertFalse(group.request_process_finalization(bubble))
        bubble.set_main_content("final result", final=True)
        self.assertTrue(group.process_finalized)
        self.assertFalse(group.process_finalization_pending)
        self.assertFalse(bubble._rendered_main_content_final)


if __name__ == "__main__":
    unittest.main()
