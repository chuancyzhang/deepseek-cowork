"""Durable, session-scoped context projections. The message ledger stays intact."""

import hashlib
import json
import sqlite3
import time

from .conversation_integrity import canonical_ledger_message


def message_fingerprint(message):
    value = canonical_ledger_message(message)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def source_fingerprints(messages):
    return [{"id": str(m["id"]), "hash": message_fingerprint(m)}
            for m in messages if isinstance(m, dict) and m.get("id")]


class ContextStorage:
    def _ensure_context_schema(self):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
            if "context_revision" not in columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN context_revision INTEGER NOT NULL DEFAULT 0")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS context_windows (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    previous_id TEXT NOT NULL DEFAULT '',
                    run_id TEXT NOT NULL,
                    history_revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    covered_ids TEXT NOT NULL,
                    source_fingerprints TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(conversation_id, ordinal)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_context_active
                    ON context_windows(conversation_id) WHERE status = 'active';
                CREATE TABLE IF NOT EXISTS context_checkpoints (
                    window_id TEXT PRIMARY KEY REFERENCES context_windows(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    runtime_state TEXT NOT NULL,
                    seed_messages TEXT NOT NULL
                );
            """)

    def context_revision(self, conversation_id):
        with self._connect() as conn:
            row = conn.execute("SELECT context_revision FROM conversations WHERE id = ?",
                               (conversation_id,)).fetchone()
            if row is None:
                return 0  # The initial UI save may still be queued; prepare is its barrier.
            return int(row[0])

    def _check_context_source(self, conn, conversation_id, fingerprints, revision):
        row = conn.execute("SELECT context_revision FROM conversations WHERE id = ?",
                           (conversation_id,)).fetchone()
        if row is None or int(row[0]) != int(revision):
            raise ValueError("历史已改变，请基于当前历史重新整理上下文。")
        expected = {str(item["id"]): item["hash"] for item in fingerprints}
        ids = list(expected)
        for start in range(0, len(ids), 400):
            batch = ids[start:start + 400]
            rows = conn.execute(
                "SELECT * FROM messages WHERE conversation_id = ? AND id IN ("
                + ",".join("?" for _ in batch) + ")", [conversation_id, *batch],
            ).fetchall()
            if len(rows) != len(batch):
                raise ValueError("部分消息尚未保存或已改变，未切换上下文。")
            for message_row in rows:
                message = self._message_row_to_dict(message_row)
                if message_fingerprint(message) != expected[message["id"]]:
                    raise ValueError("上下文来源与已保存历史不一致，未切换上下文。")

    def check_context_source(self, conversation_id, fingerprints, revision):
        with self._connect() as conn:
            self._check_context_source(conn, conversation_id, fingerprints, revision)
        return {"revision": int(revision), "message_count": len(fingerprints)}

    def get_context_window(self, conversation_id, window_id=None):
        with self._connect() as conn:
            predicate = "w.id = ?" if window_id else "w.status = 'active'"
            params = [conversation_id, window_id] if window_id else [conversation_id]
            row = conn.execute(
                "SELECT w.*, c.version, c.content, c.runtime_state, c.seed_messages "
                "FROM context_windows w JOIN context_checkpoints c ON c.window_id = w.id "
                "WHERE w.conversation_id = ? AND " + predicate, params,
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("covered_ids", "source_fingerprints", "content", "runtime_state", "seed_messages"):
            result[key] = json.loads(result[key])
        return result

    def commit_context_window(self, conversation_id, payload):
        """Compare-and-swap a projection, after its source is durably saved."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT conversation_id, status FROM context_windows WHERE id = ?",
                                    (payload["id"],)).fetchone()
            if existing:
                if existing[0] != conversation_id or existing[1] == "invalid":
                    raise ValueError("上下文提交身份已失效。")
                return {"window_id": payload["id"], "outcome": "unchanged"}
            if time.time() > float(payload["expires_at"]):
                raise ValueError("上下文提交已过期，请重新整理。")
            self._check_context_source(conn, conversation_id, payload["source_fingerprints"],
                                       payload["history_revision"])
            active = conn.execute(
                "SELECT id FROM context_windows WHERE conversation_id = ? AND status = 'active'",
                (conversation_id,),
            ).fetchone()
            if (active[0] if active else "") != payload.get("previous_id", ""):
                raise ValueError("活动上下文已改变，未重复切换。")
            ordinal = conn.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM context_windows WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            conn.execute("UPDATE context_windows SET status = 'closed' WHERE conversation_id = ? AND status = 'active'",
                         (conversation_id,))
            dump = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            conn.execute(
                "INSERT INTO context_windows VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)",
                (payload["id"], conversation_id, ordinal, payload.get("previous_id", ""),
                 payload["run_id"], payload["history_revision"], dump(payload["covered_ids"]),
                 dump(payload["source_fingerprints"]), time.time()),
            )
            conn.execute("INSERT INTO context_checkpoints VALUES (?, 1, ?, ?, ?)",
                         (payload["id"], dump(payload["content"]), dump(payload["runtime_state"]),
                          dump(payload["seed_messages"])))
        return {"window_id": payload["id"], "outcome": "committed", "ordinal": ordinal}

    def _invalidate_context_for_rewrite(self, conn, conversation_id, messages):
        """Called inside the existing, user-authorized history rewrite transaction."""
        new_hashes = {m["id"]: message_fingerprint(m) for m in messages if m.get("id")}
        new_positions = {m["id"]: index for index, m in enumerate(messages) if m.get("id")}
        old_positions = {row["id"]: row["position"] for row in conn.execute(
            "SELECT id, position FROM messages WHERE conversation_id = ?", (conversation_id,))}
        windows = conn.execute(
            "SELECT id, source_fingerprints FROM context_windows "
            "WHERE conversation_id = ? AND status != 'invalid' ORDER BY ordinal", (conversation_id,),
        ).fetchall()
        invalid = False
        last_valid = None
        for window in windows:
            fingerprints = json.loads(window["source_fingerprints"])
            ids = [item["id"] for item in fingerprints]
            invalid |= any(new_hashes.get(item["id"]) != item["hash"] for item in fingerprints)
            invalid |= sorted(ids, key=lambda mid: old_positions.get(mid, -1)) != sorted(
                ids, key=lambda mid: new_positions.get(mid, -1))
            if invalid:
                conn.execute("UPDATE context_windows SET status = 'invalid' WHERE id = ?", (window["id"],))
            else:
                last_valid = window["id"]
        conn.execute("UPDATE conversations SET context_revision = context_revision + 1 WHERE id = ?",
                     (conversation_id,))
        conn.execute("UPDATE context_windows SET history_revision = history_revision + 1 "
                     "WHERE conversation_id = ? AND status != 'invalid'", (conversation_id,))
        if invalid and last_valid:
            conn.execute("UPDATE context_windows SET status = 'active' WHERE id = ?", (last_valid,))

    def context_history_search(self, conversation_id, query, cursor=0, limit=10):
        query = str(query or "").strip()
        if not query or len(query) > 200:
            raise ValueError("请输入 1–200 个字符的检索内容。")
        limit, cursor = min(20, max(1, int(limit))), max(0, int(cursor or 0))
        deadline = time.monotonic() + 2.0
        with self._connect() as conn:
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            # Literal FTS phrase + explicit substring matching for unsegmented Chinese.
            phrase = 'content : "' + query.replace('"', '""') + '"'
            try:
                rows = conn.execute("""
                    SELECT id, role, position, created_at, substr(content, 1, 600) AS excerpt
                    FROM messages WHERE conversation_id = ? AND position >= ?
                    AND (instr(lower(content), lower(?)) > 0 OR rowid IN
                         (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?))
                    ORDER BY position LIMIT ?
                """, (conversation_id, cursor, query, phrase, limit + 1)).fetchall()
            except sqlite3.OperationalError as exc:
                if "interrupt" in str(exc).lower():
                    raise ValueError("历史检索超过 2 秒，请使用更具体的关键词。") from exc
                raise
        return {"matches": [dict(row) for row in rows[:limit]],
                "next_cursor": rows[limit]["position"] if len(rows) > limit else None}

    def context_history_read(self, conversation_id, message_id, offset=0, length=6000):
        offset, length = max(0, int(offset or 0)), min(12000, max(1, int(length)))
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM messages WHERE conversation_id = ? AND id = ?",
                               (conversation_id, str(message_id))).fetchone()
        if row is None:
            raise ValueError("当前会话中没有这条历史消息，或该消息已被用户修改。")
        message = self._message_row_to_dict(row)
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        if not content and message.get("result_obj") is not None:
            content = json.dumps(message["result_obj"], ensure_ascii=False)
        end = offset + length
        return {"message_id": message["id"], "role": message["role"], "offset": offset,
                "content": content[offset:end], "total_chars": len(content),
                "next_offset": end if end < len(content) else None}
