import errno
import os
import sqlite3
import tempfile
import unittest
import gc
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from core.chat_save_queue import (
    ChatSaveRequest,
    ChatSaveWorker,
    SAVE_ERROR_BUSY,
    SAVE_ERROR_CORRUPT,
    SAVE_ERROR_NO_SPACE,
    SAVE_ERROR_PATH_UNAVAILABLE,
    SAVE_ERROR_PERMISSION,
    SAVE_ERROR_UNKNOWN,
    classify_chat_save_error,
)
from core.chat_storage import ChatStorage, ConversationWriteConflict


class TestChatSaveQueue(unittest.TestCase):
    def test_save_error_classifier_uses_typed_error_codes(self):
        busy = sqlite3.OperationalError("busy")
        busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
        corrupt = sqlite3.DatabaseError("corrupt")
        corrupt.sqlite_errorcode = sqlite3.SQLITE_CORRUPT

        self.assertEqual(classify_chat_save_error(busy), SAVE_ERROR_BUSY)
        self.assertEqual(classify_chat_save_error(corrupt), SAVE_ERROR_CORRUPT)
        self.assertEqual(
            classify_chat_save_error(OSError(errno.ENOSPC, "full")),
            SAVE_ERROR_NO_SPACE,
        )
        self.assertEqual(
            classify_chat_save_error(PermissionError(errno.EACCES, "denied")),
            SAVE_ERROR_PERMISSION,
        )
        self.assertEqual(
            classify_chat_save_error(FileNotFoundError(errno.ENOENT, "missing")),
            SAVE_ERROR_PATH_UNAVAILABLE,
        )
        self.assertEqual(classify_chat_save_error(RuntimeError("unknown")), SAVE_ERROR_UNKNOWN)

    def test_retry_delay_uses_bounded_exponential_backoff(self):
        worker = ChatSaveWorker("unused.sqlite", debounce_ms=100)

        self.assertEqual(
            [worker._retry_delay_seconds(count) for count in (1, 2, 3, 7, 8)],
            [0.5, 1.0, 2.0, 30.0, 30.0],
        )

    def test_worker_coalesces_same_session(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            worker = ChatSaveWorker(storage.db_path, debounce_ms=20)
            completed = []
            worker.save_completed.connect(
                lambda session_id, revision: completed.append((session_id, revision))
            )
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
                        revision=1,
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
                        revision=2,
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
        self.assertEqual(completed[-1], ("session-1", 2))

    def test_wait_for_revision_is_a_durable_commit_barrier(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            worker = ChatSaveWorker(storage.db_path, debounce_ms=500)
            worker.start()
            try:
                request = ChatSaveRequest(
                    session_id="guidance-barrier",
                    messages=[
                        {"id": "u1", "role": "user", "content": "开始"},
                        {"id": "a1", "role": "assistant", "content": "已显示阶段"},
                        {"id": "g1", "role": "user", "content": "调整方向"},
                    ],
                    title="Guidance",
                    status="running",
                    meta={},
                    ready_at=0.0,
                    revision=7,
                )
                self.assertTrue(worker.enqueue(request))
                self.assertTrue(
                    worker.wait_for_revision(
                        "guidance-barrier",
                        7,
                        timeout_ms=2000,
                    )
                )
                self.assertEqual(
                    [item["id"] for item in storage.get_messages("guidance-barrier")],
                    ["u1", "a1", "g1"],
                )
            finally:
                self.assertTrue(worker.stop_worker(timeout_ms=2000))
                del worker
                del storage
                gc.collect()

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

    def test_worker_does_not_allow_same_revision_with_different_snapshot(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = ChatSaveWorker(os.path.join(temp_dir, "chat_history.sqlite"), debounce_ms=0)
            first = ChatSaveRequest(
                session_id="session-conflict",
                messages=[{"id": "m1", "role": "user", "content": "first"}],
                title="First",
                status="draft",
                meta={},
                ready_at=0.0,
                revision=4,
            )
            second = ChatSaveRequest(
                session_id="session-conflict",
                messages=[{"id": "m2", "role": "user", "content": "different"}],
                title="Different",
                status="draft",
                meta={},
                ready_at=0.0,
                revision=4,
            )

            self.assertTrue(worker.enqueue(first))
            self.assertFalse(worker.enqueue(second))

    def test_worker_ignores_stale_revision_after_newer_snapshot_is_accepted(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "chat_history.sqlite")
            storage = ChatStorage(db_path)
            worker = ChatSaveWorker(db_path, debounce_ms=0)
            worker.start()
            try:
                self.assertTrue(worker.enqueue(ChatSaveRequest(
                    session_id="session-stale",
                    messages=[{"id": "m2", "role": "user", "content": "new"}],
                    title="New",
                    status="running",
                    meta={},
                    ready_at=0.0,
                    revision=2,
                )))
                self.assertTrue(worker.enqueue(ChatSaveRequest(
                    session_id="session-stale",
                    messages=[{"id": "m1", "role": "user", "content": "old"}],
                    title="Old",
                    status="draft",
                    meta={},
                    ready_at=0.0,
                    revision=1,
                )))
                self.assertTrue(worker.flush(session_id="session-stale", timeout_ms=2000))
                messages = storage.get_messages("session-stale")
            finally:
                self.assertTrue(worker.stop_worker(timeout_ms=2000))
                del worker
                del storage
                gc.collect()
                app.processEvents()

        self.assertEqual([message["content"] for message in messages], ["new"])

    def test_history_divergence_stops_retry_and_reports_blocked_save(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = ChatSaveWorker(
                os.path.join(temp_dir, "chat_history.sqlite"),
                debounce_ms=0,
            )
            blocked = []
            retryable = []
            worker.save_blocked.connect(
                lambda session_id, revision, error: blocked.append(
                    (session_id, revision, error)
                )
            )
            worker.save_failed.connect(
                lambda session_id, revision, category, error: retryable.append(
                    (session_id, revision, category, error)
                )
            )
            request = ChatSaveRequest(
                session_id="session-diverged",
                messages=[{"id": "u1", "role": "user", "content": "new"}],
                title="Diverged",
                status="completed",
                meta={},
                ready_at=0.0,
                revision=2,
            )
            with patch.object(
                ChatStorage,
                "save_conversation_safely",
                side_effect=ConversationWriteConflict("history diverged"),
            ):
                worker.start()
                try:
                    self.assertTrue(worker.enqueue(request))
                    self.assertFalse(
                        worker.wait_for_revision(
                            "session-diverged",
                            2,
                            timeout_ms=2000,
                        )
                    )
                    app.processEvents()
                finally:
                    self.assertTrue(worker.stop_worker(timeout_ms=2000))
                    del worker
                    gc.collect()
                    app.processEvents()

        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0][:2], ("session-diverged", 2))
        self.assertIn("history diverged", blocked[0][2])
        self.assertEqual(retryable, [])


if __name__ == "__main__":
    unittest.main()
