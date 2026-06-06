import os
import tempfile
import unittest
import gc

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.chat_save_queue import ChatSaveRequest, ChatSaveWorker
from core.chat_storage import ChatStorage


class TestChatSaveQueue(unittest.TestCase):
    def test_worker_coalesces_same_session(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            worker = ChatSaveWorker(storage.db_path, debounce_ms=20)
            worker.start()
            try:
                worker.enqueue(
                    ChatSaveRequest(
                        session_id="session-1",
                        messages=[{"id": "m1", "role": "user", "content": "first"}],
                        title="First",
                        status="draft",
                        meta={"workspace_dir": "A"},
                        ready_at=0.0,
                    )
                )
                worker.enqueue(
                    ChatSaveRequest(
                        session_id="session-1",
                        messages=[{"id": "m2", "role": "user", "content": "second"}],
                        title="Second",
                        status="running",
                        meta={"workspace_dir": "B"},
                        ready_at=0.0,
                    )
                )
                self.assertTrue(worker.flush(session_id="session-1", timeout_ms=2000))
                app.processEvents()
                record = storage.get_conversation_record("session-1") or {}
                messages = storage.get_messages("session-1")
            finally:
                self.assertTrue(worker.stop_worker(timeout_ms=2000))
                del worker
                del storage
                gc.collect()
                app.processEvents()

        self.assertEqual(record.get("title"), "Second")
        self.assertEqual(record.get("status"), "running")
        self.assertEqual((record.get("meta") or {}).get("workspace_dir"), "B")
        self.assertEqual([message.get("content") for message in messages], ["second"])

    def test_worker_saves_multiple_sessions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            worker = ChatSaveWorker(storage.db_path, debounce_ms=10)
            worker.start()
            try:
                worker.enqueue(
                    ChatSaveRequest(
                        session_id="session-a",
                        messages=[{"id": "a1", "role": "user", "content": "alpha"}],
                        title="Alpha",
                        status="draft",
                        meta={"workspace_dir": "A"},
                        ready_at=0.0,
                    )
                )
                worker.enqueue(
                    ChatSaveRequest(
                        session_id="session-b",
                        messages=[{"id": "b1", "role": "user", "content": "beta"}],
                        title="Beta",
                        status="completed",
                        meta={"workspace_dir": "B"},
                        ready_at=0.0,
                    )
                )
                self.assertTrue(worker.flush(timeout_ms=2000))
                record_a = storage.get_conversation_record("session-a") or {}
                record_b = storage.get_conversation_record("session-b") or {}
            finally:
                self.assertTrue(worker.stop_worker(timeout_ms=2000))
                del worker
                del storage
                gc.collect()

        self.assertEqual(record_a.get("title"), "Alpha")
        self.assertEqual(record_b.get("status"), "completed")


if __name__ == "__main__":
    unittest.main()
