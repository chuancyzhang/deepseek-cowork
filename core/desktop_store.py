"""Desktop commands and view state beside the existing conversation ledger.

Accepting a submission commits its message and dispatch record together. This
module contains no UI objects and does not change provider-message projection.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import sqlite3
import time

from core.chat_storage import ChatStorage


class DesktopChatStorage(ChatStorage):
    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        from core.sidebar_search import search_tokens
        connection.create_function("sidebar_search_tokens", 1, search_tokens, deterministic=True)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class DesktopStore:
    def __init__(self, storage):
        self.storage = storage
        with storage._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS desktop_runs (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    message_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS desktop_runs_conversation
                    ON desktop_runs(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS desktop_view_state (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0
                );
            """)

    def accept(self, payload):
        conversation_id, run_id = payload["conversation_id"], payload["request_id"]
        message_id = payload["message_id"]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        now = time.time_ns()
        with self.storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM desktop_runs WHERE id=?", (run_id,)).fetchone()
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise ValueError("请求标识已用于另一条消息，请重新发送。")
                return dict(existing), False
            active = db.execute("SELECT id FROM desktop_runs WHERE conversation_id=? AND status IN ('queued','running','stopping')", (conversation_id,)).fetchone()
            if active:
                raise ValueError("当前对话仍在运行，请发送补充或先停止任务。")
            record = db.execute("SELECT meta FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            if not record:
                raise ValueError("对话不存在，请重新打开。")
            position = db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM messages WHERE conversation_id=?", (conversation_id,)).fetchone()[0]
            message = {"id": message_id, "role": "user", "content": payload["content"],
                       "created_at": int(time.time()), "meta": {"turn_id": run_id, "request_id": run_id,
                       "sequence": position, "desktop_attachments": payload.get("attachments", [])}}
            self.storage._insert_message_row(db, "messages", "conversation_id", conversation_id, message, position, int(time.time()))
            db.execute("UPDATE conversations SET updated_at=?,title=CASE WHEN title='新对话' THEN ? ELSE title END WHERE id=?",
                       (int(time.time()), payload["display_text"][:32] or "附件任务", conversation_id))
            db.execute("INSERT INTO desktop_runs(id,conversation_id,message_id,fingerprint,status,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                       (run_id, conversation_id, message_id, fingerprint, "queued", raw, now, now))
        return self.get_run(run_id), True

    def get_run(self, run_id):
        with self.storage._connect() as db:
            row = db.execute("SELECT * FROM desktop_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def latest_run(self, conversation_id):
        with self.storage._connect() as db:
            row = db.execute("SELECT * FROM desktop_runs WHERE conversation_id=? ORDER BY created_at DESC LIMIT 1", (conversation_id,)).fetchone()
        return dict(row) if row else None

    def conversation_runs(self, conversation_id):
        with self.storage._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM desktop_runs WHERE conversation_id=? ORDER BY created_at", (conversation_id,))]

    def set_run(self, run_id, status, error=""):
        with self.storage._connect() as db:
            db.execute("UPDATE desktop_runs SET status=?,error=?,updated_at=? WHERE id=?", (status, str(error), time.time_ns(), run_id))

    def active_runs(self):
        with self.storage._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM desktop_runs WHERE status IN ('queued','running','stopping')")]

    def get_state(self, key, default=None):
        with self.storage._connect() as db:
            row = db.execute("SELECT value FROM desktop_view_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put_state(self, key, value):
        with self.storage._connect() as db:
            db.execute("INSERT INTO desktop_view_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, revision=revision+1",
                       (key, json.dumps(value, ensure_ascii=False)))
        return {"saved": True}
