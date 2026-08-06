import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime

from .conversation_integrity import normalize_message_ids


logger = logging.getLogger(__name__)

AGENT_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "closed",
    "killed",
    "failed_recovered",
}

MESSAGE_JSON_COLUMNS = {
    "messages": {
        "content_parts": "TEXT",
        "meta": "TEXT",
        "result_obj": "TEXT",
    },
    "agent_messages": {
        "content_parts": "TEXT",
        "meta": "TEXT",
        "result_obj": "TEXT",
    },
}

LEGACY_PLAN_META_KEYS = {
    "plan_mode_enabled",
    "plan_config",
    "plan_phase",
    "plan_protocol_version",
    "plan_mode_state",
    "plan_document",
    "pending_plan_questions",
}


class ChatStorage:
    def __init__(self, db_path):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error:
            pass
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    created_at INTEGER,
                    updated_at INTEGER,
                    status TEXT,
                    meta TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    reasoning_content TEXT,
                    content_parts TEXT,
                    meta TEXT,
                    result_obj TEXT,
                    token_count INTEGER,
                    tool_call_id TEXT,
                    position INTEGER,
                    created_at INTEGER,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_conversation_pos
                ON messages(conversation_id, position)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS deliverables (
                    id TEXT PRIMARY KEY,
                    workspace_path TEXT NOT NULL,
                    workspace_key TEXT NOT NULL,
                    path TEXT NOT NULL,
                    path_key TEXT NOT NULL,
                    conversation_id TEXT,
                    source TEXT NOT NULL,
                    created_at INTEGER,
                    updated_at INTEGER,
                    UNIQUE(workspace_key, path_key),
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE SET NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_deliverables_workspace_updated
                ON deliverables(workspace_key, updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inline_visualizations (
                    conversation_id TEXT NOT NULL,
                    file TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    title TEXT,
                    origins TEXT,
                    created_at INTEGER,
                    PRIMARY KEY (conversation_id, file),
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inline_visualization_states (
                    conversation_id TEXT NOT NULL,
                    file TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    state TEXT,
                    updated_at INTEGER,
                    PRIMARY KEY (conversation_id, file),
                    FOREIGN KEY(conversation_id, file)
                        REFERENCES inline_visualizations(conversation_id, file) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    parent_message_id TEXT,
                    name TEXT,
                    status TEXT NOT NULL,
                    is_subagent INTEGER NOT NULL DEFAULT 1,
                    fork_context INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER,
                    updated_at INTEGER,
                    started_at INTEGER,
                    finished_at INTEGER,
                    last_error TEXT,
                    last_result TEXT,
                    meta TEXT,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agents_conversation_updated
                ON agents(conversation_id, updated_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agents_conversation_status
                ON agents(conversation_id, status)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_messages (
                    id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    reasoning_content TEXT,
                    content_parts TEXT,
                    meta TEXT,
                    result_obj TEXT,
                    token_count INTEGER,
                    tool_call_id TEXT,
                    position INTEGER,
                    created_at INTEGER,
                    FOREIGN KEY(agent_id) REFERENCES agents(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_messages_agent_pos
                ON agent_messages(agent_id, position)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS im_sessions (
                    provider TEXT NOT NULL,
                    im_user_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    created_at INTEGER,
                    updated_at INTEGER,
                    PRIMARY KEY (provider, im_user_id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_im_sessions_conversation
                ON im_sessions(conversation_id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS im_daily_summaries (
                    provider TEXT NOT NULL,
                    im_user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    summary_date TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    summary_text TEXT,
                    source_message_upto_pos INTEGER,
                    token_estimate INTEGER,
                    created_at INTEGER,
                    updated_at INTEGER,
                    PRIMARY KEY (provider, im_user_id, chat_id, summary_date)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_im_daily_summaries_conversation_date
                ON im_daily_summaries(conversation_id, summary_date)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_im_daily_summaries_identity_date
                ON im_daily_summaries(provider, im_user_id, chat_id, summary_date)
                """
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    content,
                    reasoning_content,
                    content='messages',
                    content_rowid='rowid'
                )
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, content, reasoning_content)
                    VALUES (new.rowid, new.content, new.reasoning_content);
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content, reasoning_content)
                    VALUES('delete', old.rowid, old.content, old.reasoning_content);
                END
                """
            )
            conn.execute(
                """
                CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content, reasoning_content)
                    VALUES('delete', old.rowid, old.content, old.reasoning_content);
                    INSERT INTO messages_fts(rowid, content, reasoning_content)
                    VALUES (new.rowid, new.content, new.reasoning_content);
                END
                """
            )
            self._ensure_message_columns(conn)
            self._cleanup_legacy_plan_meta(conn)

    def _parse_json_dict(self, text):
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _parse_json_value(self, text, default=None):
        if text is None or text == "":
            return default
        try:
            return json.loads(text)
        except Exception:
            return default

    def _json_dumps(self, value):
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    def _strip_legacy_plan_meta(self, meta):
        if not isinstance(meta, dict):
            return meta
        cleaned = dict(meta)
        for key in LEGACY_PLAN_META_KEYS:
            cleaned.pop(key, None)
        return cleaned

    def _cleanup_legacy_plan_meta(self, conn):
        rows = conn.execute("SELECT id, meta FROM conversations WHERE meta IS NOT NULL AND meta != ''").fetchall()
        for row in rows:
            meta = self._parse_json_dict(row["meta"])
            if not meta or not any(key in meta for key in LEGACY_PLAN_META_KEYS):
                continue
            cleaned = self._strip_legacy_plan_meta(meta)
            conn.execute(
                "UPDATE conversations SET meta = ? WHERE id = ?",
                (self._json_dumps(cleaned), row["id"]),
            )

    def _ensure_message_columns(self, conn):
        for table_name, columns in MESSAGE_JSON_COLUMNS.items():
            existing_columns = {
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
            for column_name, column_type in columns.items():
                if column_name in existing_columns:
                    continue
                conn.execute(
                    f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                )

    def _agent_row_to_dict(self, row):
        if not row:
            return None
        return {
            "id": row["id"],
            "conversation_id": row["conversation_id"],
            "parent_message_id": row["parent_message_id"],
            "name": row["name"],
            "status": row["status"] or "queued",
            "is_subagent": bool(row["is_subagent"]),
            "fork_context": bool(row["fork_context"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "last_error": row["last_error"],
            "last_result": row["last_result"],
            "meta": self._parse_json_dict(row["meta"]),
        }

    def _message_row_to_dict(self, row):
        msg = {"id": row["id"], "role": row["role"], "content": row["content"]}
        if row["tool_calls"]:
            msg["tool_calls"] = json.loads(row["tool_calls"])
        if row["reasoning_content"] is not None:
            msg["reasoning_content"] = row["reasoning_content"]
            msg["reasoning"] = row["reasoning_content"]
        content_parts = self._parse_json_value(row["content_parts"])
        if isinstance(content_parts, list):
            msg["content_parts"] = content_parts
        meta = self._parse_json_value(row["meta"], default={})
        if isinstance(meta, dict) and meta:
            msg["meta"] = meta
        result_obj = self._parse_json_value(row["result_obj"])
        if result_obj is not None:
            msg["result_obj"] = result_obj
        if row["token_count"] is not None:
            msg["token_count"] = row["token_count"]
        if row["tool_call_id"] is not None:
            msg["tool_call_id"] = row["tool_call_id"]
        if "created_at" in row.keys() and row["created_at"] is not None:
            msg["created_at"] = row["created_at"]
        return msg

    def _insert_message_row(self, conn, table_name, owner_column, owner_id, msg, index, now):
        msg_id = msg.get("id") or uuid.uuid4().hex
        msg["id"] = msg_id
        tool_calls = msg.get("tool_calls")
        tool_calls_json = (
            json.dumps(tool_calls, ensure_ascii=False) if tool_calls is not None else None
        )
        reasoning_content = msg.get("reasoning_content") or msg.get("reasoning")
        content_parts_json = self._json_dumps(msg.get("content_parts"))
        meta_json = self._json_dumps(msg.get("meta"))
        result_obj_json = self._json_dumps(msg.get("result_obj"))
        conn.execute(
            f"""
            INSERT INTO {table_name} (
                id, {owner_column}, role, content, tool_calls, reasoning_content,
                content_parts, meta, result_obj,
                token_count, tool_call_id, position, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                msg_id,
                owner_id,
                msg.get("role"),
                msg.get("content"),
                tool_calls_json,
                reasoning_content,
                content_parts_json,
                meta_json,
                result_obj_json,
                msg.get("token_count"),
                msg.get("tool_call_id"),
                index,
                msg.get("created_at") or now,
            ),
        )

    def _existing_messages_are_prefix(self, rows, normalized_messages):
        if len(rows) > len(normalized_messages):
            return False
        for index, row in enumerate(rows):
            current = self._message_row_to_dict(row)
            incoming = normalized_messages[index]
            if current.get("id") != incoming.get("id"):
                return False
            if self._message_signature(current) != self._message_signature(incoming):
                return False
        return True

    def _message_signature(self, message):
        if not isinstance(message, dict):
            return None
        signature = {
            "role": message.get("role") or "",
            "content": message.get("content") or "",
            "tool_call_id": message.get("tool_call_id") or "",
            "reasoning_content": message.get("reasoning_content") or message.get("reasoning") or "",
        }
        content_parts = message.get("content_parts")
        if isinstance(content_parts, list):
            signature["content_parts"] = content_parts
        meta = message.get("meta")
        if isinstance(meta, dict) and meta:
            signature["meta"] = meta
        result_obj = message.get("result_obj")
        if result_obj is not None:
            signature["result_obj"] = result_obj
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            normalized_calls = []
            for tool_call in tool_calls:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function") if isinstance(tool_call.get("function"), dict) else {}
                arguments = function.get("arguments")
                if isinstance(arguments, (dict, list)):
                    try:
                        arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
                    except Exception:
                        arguments = str(arguments)
                elif arguments is None:
                    arguments = ""
                normalized_calls.append(
                    {
                        "id": tool_call.get("id") or "",
                        "type": tool_call.get("type") or "function",
                        "name": function.get("name") or "",
                        "arguments": arguments,
                    }
                )
            signature["tool_calls"] = normalized_calls
        try:
            return json.dumps(signature, ensure_ascii=False, sort_keys=True)
        except Exception:
            return None

    def normalize_messages(self, messages, conversation_id=""):
        """Normalize message identities without dropping legacy history."""
        normalized, repairs = normalize_message_ids(
            messages,
            conversation_id=conversation_id,
        )
        if repairs:
            logger.warning(
                "conversation_history_identity_repaired",
                extra={
                    "conversation_id": str(conversation_id or ""),
                    "repair_count": len(repairs),
                    "repair_kinds": sorted({str(item.get("kind") or "") for item in repairs}),
                },
            )
        return normalized

    def upsert_conversation(self, conversation_id, title=None, status="active", meta=None):
        with self._connect() as conn:
            self._upsert_conversation_in_connection(
                conn,
                conversation_id,
                title=title,
                status=status,
                meta=meta,
            )

    def _upsert_conversation_in_connection(
        self,
        conn,
        conversation_id,
        title=None,
        status="active",
        meta=None,
    ):
        now = int(time.time())
        meta = self._strip_legacy_plan_meta(meta)
        meta_json = json.dumps(meta, ensure_ascii=False) if meta is not None else None
        existing = conn.execute(
            "SELECT id, created_at, meta FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if existing:
            if meta is None:
                meta_json = existing["meta"]
            conn.execute(
                """
                UPDATE conversations
                SET title = ?, updated_at = ?, status = ?, meta = ?
                WHERE id = ?
                """,
                (title, now, status, meta_json, conversation_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO conversations (id, title, created_at, updated_at, status, meta)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, title, now, now, status, meta_json),
            )

    def get_conversation_meta(self, conversation_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT meta FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        if not row:
            return {}
        raw_meta = row["meta"]
        if not raw_meta:
            return {}
        try:
            parsed = json.loads(raw_meta)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def get_conversation_record(self, conversation_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, updated_at, status, meta FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        if not row:
            return None
        meta = {}
        if row["meta"]:
            try:
                meta = json.loads(row["meta"])
                if not isinstance(meta, dict):
                    meta = {}
            except Exception:
                meta = {}
        return {
            "id": row["id"],
            "title": row["title"],
            "updated_at": row["updated_at"],
            "status": row["status"] or "active",
            "meta": meta,
        }

    def get_message_owners(self, message_ids):
        normalized = [str(item or "").strip() for item in message_ids or []]
        normalized = list(dict.fromkeys(item for item in normalized if item))
        if not normalized:
            return {}
        placeholders = ",".join("?" for _ in normalized)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, conversation_id FROM messages WHERE id IN ({placeholders})",
                normalized,
            ).fetchall()
        return {str(row["id"]): str(row["conversation_id"]) for row in rows}

    def update_conversation_meta(self, conversation_id, meta_patch):
        if not isinstance(meta_patch, dict):
            raise TypeError("meta_patch must be a dict")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT meta FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if not row:
                raise KeyError(f"conversation not found: {conversation_id}")
            meta = self._parse_json_dict(row["meta"])
            meta.update(meta_patch)
            conn.execute(
                "UPDATE conversations SET meta = ? WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False), conversation_id),
            )
        return self.get_conversation_record(conversation_id)

    def upsert_agent(
        self,
        agent_id,
        conversation_id,
        parent_message_id=None,
        name=None,
        status="queued",
        is_subagent=True,
        fork_context=False,
        created_at=None,
        updated_at=None,
        started_at=None,
        finished_at=None,
        last_error=None,
        last_result=None,
        meta=None,
    ):
        now = int(time.time())
        created_ts = created_at or now
        updated_ts = updated_at or now
        meta_json = json.dumps(meta, ensure_ascii=False) if isinstance(meta, dict) else None
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT id, created_at, parent_message_id, name, status, is_subagent, fork_context,
                       started_at, finished_at, last_error, last_result, meta
                FROM agents
                WHERE id = ?
                """,
                (agent_id,),
            ).fetchone()
            if existing:
                created_ts = existing["created_at"] or created_ts
                if parent_message_id is None:
                    parent_message_id = existing["parent_message_id"]
                if name is None:
                    name = existing["name"]
                if status is None:
                    status = existing["status"]
                if is_subagent is None:
                    is_subagent = bool(existing["is_subagent"])
                if fork_context is None:
                    fork_context = bool(existing["fork_context"])
                if started_at is None:
                    started_at = existing["started_at"]
                if finished_at is None:
                    finished_at = existing["finished_at"]
                if last_error is None:
                    last_error = existing["last_error"]
                if last_result is None:
                    last_result = existing["last_result"]
                if meta_json is None:
                    meta_json = existing["meta"]
                conn.execute(
                    """
                    UPDATE agents
                    SET conversation_id = ?, parent_message_id = ?, name = ?, status = ?,
                        is_subagent = ?, fork_context = ?, updated_at = ?, started_at = ?,
                        finished_at = ?, last_error = ?, last_result = ?, meta = ?
                    WHERE id = ?
                    """,
                    (
                        conversation_id,
                        parent_message_id,
                        name,
                        status or "queued",
                        1 if is_subagent else 0,
                        1 if fork_context else 0,
                        updated_ts,
                        started_at,
                        finished_at,
                        last_error,
                        last_result,
                        meta_json,
                        agent_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO agents (
                        id, conversation_id, parent_message_id, name, status, is_subagent, fork_context,
                        created_at, updated_at, started_at, finished_at, last_error, last_result, meta
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        agent_id,
                        conversation_id,
                        parent_message_id,
                        name,
                        status or "queued",
                        1 if is_subagent else 0,
                        1 if fork_context else 0,
                        created_ts,
                        updated_ts,
                        started_at,
                        finished_at,
                        last_error,
                        last_result,
                        meta_json,
                    ),
                )
        return self.get_agent(agent_id)

    def get_agent(self, agent_id):
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, conversation_id, parent_message_id, name, status, is_subagent, fork_context,
                       created_at, updated_at, started_at, finished_at, last_error, last_result, meta
                FROM agents
                WHERE id = ?
                """,
                (agent_id,),
            ).fetchone()
        return self._agent_row_to_dict(row)

    def list_agents(self, conversation_id, status_filter=None):
        clauses = ["conversation_id = ?"]
        params = [conversation_id]
        status_values = []
        if isinstance(status_filter, str) and status_filter.strip():
            status_values = [status_filter.strip()]
        elif isinstance(status_filter, (list, tuple, set)):
            status_values = [str(item).strip() for item in status_filter if str(item).strip()]
        if status_values:
            placeholders = ",".join(["?"] * len(status_values))
            clauses.append(f"status IN ({placeholders})")
            params.extend(status_values)
        sql = (
            "SELECT id, conversation_id, parent_message_id, name, status, is_subagent, fork_context,"
            " created_at, updated_at, started_at, finished_at, last_error, last_result, meta"
            " FROM agents WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC, created_at DESC"
        )
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._agent_row_to_dict(row) for row in rows]

    def replace_agent_messages(self, agent_id, messages):
        normalized_messages = self.normalize_messages(messages, conversation_id=f"agent:{agent_id}")
        now = int(time.time())
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, tool_calls, reasoning_content, content_parts, meta,
                       result_obj, token_count, tool_call_id, created_at
                FROM agent_messages
                WHERE agent_id = ?
                ORDER BY position ASC
                """,
                (agent_id,),
            ).fetchall()
            if rows and self._existing_messages_are_prefix(rows, normalized_messages):
                start = len(rows)
                for index, msg in enumerate(normalized_messages[start:], start=start):
                    self._insert_message_row(
                        conn,
                        "agent_messages",
                        "agent_id",
                        agent_id,
                        msg,
                        index,
                        now,
                    )
                return
            conn.execute("DELETE FROM agent_messages WHERE agent_id = ?", (agent_id,))
            for index, msg in enumerate(normalized_messages):
                self._insert_message_row(
                    conn,
                    "agent_messages",
                    "agent_id",
                    agent_id,
                    msg,
                    index,
                    now,
                )

    def get_agent_messages(self, agent_id):
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, tool_calls, reasoning_content, content_parts, meta,
                       result_obj, token_count, tool_call_id, created_at
                FROM agent_messages
                WHERE agent_id = ?
                ORDER BY position ASC
                """,
                (agent_id,),
            ).fetchall()
        messages = []
        for row in rows:
            messages.append(self._message_row_to_dict(row))
        normalized_messages = self.normalize_messages(messages, conversation_id=f"agent:{agent_id}")
        try:
            changed = json.dumps(messages, ensure_ascii=False, sort_keys=True) != json.dumps(
                normalized_messages,
                ensure_ascii=False,
                sort_keys=True,
            )
        except Exception:
            changed = messages != normalized_messages
        if changed:
            self.replace_agent_messages(agent_id, normalized_messages)
        return normalized_messages

    def append_agent_messages(self, agent_id, new_messages):
        existing = self.get_agent_messages(agent_id)
        payload = existing + (new_messages if isinstance(new_messages, list) else [])
        self.replace_agent_messages(agent_id, payload)
        return self.get_agent_messages(agent_id)

    def set_agent_status(self, agent_id, status, last_error=None, last_result=None, meta_patch=None):
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT status, started_at, finished_at, last_error, last_result, meta
                FROM agents
                WHERE id = ?
                """,
                (agent_id,),
            ).fetchone()
            if not row:
                return None
            started_at = row["started_at"]
            finished_at = row["finished_at"]
            if status == "running" and not started_at:
                started_at = now
            if status in AGENT_TERMINAL_STATUSES and not finished_at:
                finished_at = now
            merged_error = last_error if last_error is not None else row["last_error"]
            merged_result = last_result if last_result is not None else row["last_result"]
            merged_meta = self._parse_json_dict(row["meta"])
            if isinstance(meta_patch, dict):
                merged_meta.update(meta_patch)
            conn.execute(
                """
                UPDATE agents
                SET status = ?, updated_at = ?, started_at = ?, finished_at = ?,
                    last_error = ?, last_result = ?, meta = ?
                WHERE id = ?
                """,
                (
                    status,
                    now,
                    started_at,
                    finished_at,
                    merged_error,
                    merged_result,
                    json.dumps(merged_meta, ensure_ascii=False),
                    agent_id,
                ),
            )
        return self.get_agent(agent_id)

    def resolve_agent_target(self, conversation_id, target):
        target_text = str(target or "").strip()
        if not target_text:
            raise ValueError("target is required")
        agent = self.get_agent(target_text)
        if agent and agent.get("conversation_id") == conversation_id:
            return agent
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, conversation_id, parent_message_id, name, status, is_subagent, fork_context,
                       created_at, updated_at, started_at, finished_at, last_error, last_result, meta
                FROM agents
                WHERE conversation_id = ? AND name = ?
                ORDER BY updated_at DESC
                """,
                (conversation_id, target_text),
            ).fetchall()
        if not rows:
            raise ValueError(f"agent '{target_text}' not found in current conversation")
        if len(rows) > 1:
            raise ValueError(f"agent name '{target_text}' is ambiguous in current conversation")
        return self._agent_row_to_dict(rows[0])

    def delete_agent(self, agent_id, hard=False):
        if hard:
            with self._connect() as conn:
                conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
            return {"deleted": True, "hard": True}
        updated = self.set_agent_status(agent_id, "closed")
        return {"deleted": bool(updated), "hard": False, "agent": updated}

    def replace_messages(self, conversation_id, messages):
        normalized_messages = self.normalize_messages(messages, conversation_id=conversation_id)
        with self._connect() as conn:
            normalized_messages = self._remap_cross_conversation_message_ids(
                conn,
                conversation_id,
                normalized_messages,
            )
            self._replace_messages_in_connection(conn, conversation_id, normalized_messages)

    def _remap_cross_conversation_message_ids(
        self,
        conn,
        conversation_id,
        normalized_messages,
    ):
        message_ids = [
            str(message.get("id") or "").strip()
            for message in normalized_messages
            if isinstance(message, dict) and str(message.get("id") or "").strip()
        ]
        owners = {}
        if message_ids:
            unique_ids = list(dict.fromkeys(message_ids))
            placeholders = ",".join("?" for _ in unique_ids)
            rows = conn.execute(
                f"SELECT id, conversation_id FROM messages WHERE id IN ({placeholders})",
                unique_ids,
            ).fetchall()
            owners = {str(row["id"]): str(row["conversation_id"]) for row in rows}
        remapped = []
        seen = set()
        for index, message in enumerate(normalized_messages):
            normalized_message = dict(message)
            message_id = str(normalized_message.get("id") or "").strip()
            owner = owners.get(message_id)
            conflict = bool(
                message_id
                and (
                    (owner and owner != conversation_id)
                    or message_id in seen
                )
            )
            if conflict:
                original_id = message_id
                suffix = index if message_id in seen else 0
                message_id = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"deepseek-cowork-message:{conversation_id}:{original_id}:{suffix}",
                ).hex
                while message_id in seen:
                    suffix += 1
                    message_id = uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"deepseek-cowork-message:{conversation_id}:{original_id}:{suffix}",
                    ).hex
                meta = (
                    dict(normalized_message.get("meta") or {})
                    if isinstance(normalized_message.get("meta"), dict)
                    else {}
                )
                meta["original_message_id"] = original_id
                meta["message_id_remapped"] = True
                normalized_message["meta"] = meta
                normalized_message["id"] = message_id
                logger.warning(
                    "conversation_message_id_cross_owner_repaired",
                    extra={
                        "conversation_id": str(conversation_id or ""),
                        "original_message_id": original_id,
                        "new_message_id": message_id,
                    },
                )
            seen.add(message_id)
            remapped.append(normalized_message)
        return remapped

    def _replace_messages_in_connection(self, conn, conversation_id, normalized_messages):
        now = int(time.time())
        rows = conn.execute(
            """
            SELECT id, role, content, tool_calls, reasoning_content, content_parts, meta,
                   result_obj, token_count, tool_call_id, created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY position ASC
            """,
            (conversation_id,),
        ).fetchall()
        if rows and self._existing_messages_are_prefix(rows, normalized_messages):
            start = len(rows)
            for index, msg in enumerate(normalized_messages[start:], start=start):
                self._insert_message_row(
                    conn,
                    "messages",
                    "conversation_id",
                    conversation_id,
                    msg,
                    index,
                    now,
                )
            return
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        for index, msg in enumerate(normalized_messages):
            self._insert_message_row(
                conn,
                "messages",
                "conversation_id",
                conversation_id,
                msg,
                index,
                now,
            )

    def save_conversation(self, conversation_id, messages, title=None, status="active", meta=None):
        normalized_messages = self.normalize_messages(messages, conversation_id=conversation_id)
        with self._connect() as conn:
            normalized_messages = self._remap_cross_conversation_message_ids(
                conn,
                conversation_id,
                normalized_messages,
            )
            self._upsert_conversation_in_connection(
                conn,
                conversation_id,
                title=title,
                status=status,
                meta=meta,
            )
            self._replace_messages_in_connection(conn, conversation_id, normalized_messages)

    def _conversation_rows_to_dicts(self, rows):
        conversations = []
        for row in rows:
            meta = {}
            if row["meta"]:
                try:
                    parsed = json.loads(row["meta"])
                    if isinstance(parsed, dict):
                        meta = parsed
                except Exception:
                    meta = {}
            conversations.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "updated_at": row["updated_at"],
                    "status": row["status"] or "active",
                    "meta": meta,
                    "im_provider": row["im_provider"],
                }
            )
        return conversations

    def list_conversation_summaries(self, limit=None, offset=0):
        with self._connect() as conn:
            params = []
            limit_clause = ""
            if limit is not None:
                limit_clause = "LIMIT ? OFFSET ?"
                params.extend([max(0, int(limit)), max(0, int(offset or 0))])
            rows = conn.execute(
                f"""
                SELECT c.id, c.title, c.updated_at, c.status, c.meta, im.provider AS im_provider
                FROM conversations c
                LEFT JOIN (
                    SELECT conversation_id, MIN(provider) AS provider
                    FROM im_sessions
                    GROUP BY conversation_id
                ) im ON im.conversation_id = c.id
                ORDER BY c.updated_at DESC
                {limit_clause}
                """,
                params,
            ).fetchall()
        return self._conversation_rows_to_dicts(rows)

    def list_conversations(self):
        return self.list_conversation_summaries()

    def list_conversations_by_workspace(self):
        grouped = {}
        for conversation in self.list_conversations():
            meta = conversation.get("meta") or {}
            if str(meta.get("workspace_source") or "").strip().lower() == "chat":
                continue
            workspace_dir = str(meta.get("workspace_dir") or "").strip()
            if not workspace_dir:
                continue
            key = os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(workspace_dir))))
            item = dict(conversation)
            item["workspace_dir"] = os.path.normpath(os.path.abspath(os.path.expanduser(workspace_dir)))
            grouped.setdefault(key, []).append(item)
        return grouped

    def list_unassigned_conversations(self):
        result = []
        for conversation in self.list_conversations():
            meta = conversation.get("meta") or {}
            if str(meta.get("workspace_source") or "").strip().lower() == "chat":
                result.append(conversation)
                continue
            if str(meta.get("workspace_dir") or "").strip():
                continue
            result.append(conversation)
        return result

    def list_archived_conversations(self):
        result = []
        for conversation in self.list_conversations():
            meta = conversation.get("meta") or {}
            if meta.get("archived"):
                result.append(conversation)
        return result

    def restore_conversation(self, conversation_id):
        if not str(conversation_id or "").strip():
            return None
        return self.update_conversation_meta(conversation_id, {"archived": False})

    def archive_conversations_for_workspace(self, workspace_dir):
        target = str(workspace_dir or "").strip()
        if not target:
            return 0
        target_key = os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(target))))
        archived = 0
        with self._connect() as conn:
            rows = conn.execute("SELECT id, title, status, meta FROM conversations").fetchall()
            for row in rows:
                meta = self._parse_json_dict(row["meta"])
                if str(meta.get("workspace_source") or "").strip().lower() == "chat":
                    continue
                row_workspace = str(meta.get("workspace_dir") or "").strip()
                if not row_workspace:
                    continue
                row_key = os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(row_workspace))))
                if row_key != target_key:
                    continue
                if meta.get("archived"):
                    continue
                meta["archived"] = True
                conn.execute(
                    "UPDATE conversations SET status = ?, meta = ? WHERE id = ?",
                    (
                        row["status"] or "completed",
                        json.dumps(meta, ensure_ascii=False),
                        row["id"],
                    ),
                )
                archived += 1
        return archived

    def search_conversations(self, query, limit=50):
        text = str(query or "").strip()
        if not text:
            return []
        limit = max(1, int(limit or 50))
        like_query = f"%{text}%"
        matches = {}

        def add_match(conversation_id, score):
            if not conversation_id:
                return
            current = matches.get(conversation_id)
            if current is None or score < current:
                matches[conversation_id] = score

        with self._connect() as conn:
            title_rows = conn.execute(
                """
                SELECT id
                FROM conversations
                WHERE title LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (like_query, limit),
            ).fetchall()
            for index, row in enumerate(title_rows):
                add_match(row["id"], index)

            fts_query = '"' + text.replace('"', '""') + '"'
            try:
                message_rows = conn.execute(
                    """
                    SELECT m.conversation_id, MIN(m.position) AS first_position
                    FROM messages_fts f
                    JOIN messages m ON m.rowid = f.rowid
                    WHERE messages_fts MATCH ?
                    GROUP BY m.conversation_id
                    LIMIT ?
                    """,
                    (fts_query, limit),
                ).fetchall()
            except sqlite3.Error:
                message_rows = conn.execute(
                    """
                    SELECT conversation_id, MIN(position) AS first_position
                    FROM messages
                    WHERE content LIKE ? OR reasoning_content LIKE ?
                    GROUP BY conversation_id
                    LIMIT ?
                    """,
                    (like_query, like_query, limit),
                ).fetchall()
            for row in message_rows:
                add_match(row["conversation_id"], 1000 + int(row["first_position"] or 0))

            like_rows = conn.execute(
                """
                SELECT conversation_id, MIN(position) AS first_position
                FROM messages
                WHERE content LIKE ? OR reasoning_content LIKE ?
                GROUP BY conversation_id
                LIMIT ?
                """,
                (like_query, like_query, limit),
            ).fetchall()
            for row in like_rows:
                add_match(row["conversation_id"], 2000 + int(row["first_position"] or 0))

        return [
            conversation_id
            for conversation_id, _score in sorted(matches.items(), key=lambda item: item[1])
        ][:limit]

    def iter_conversation_transcripts(self, include_archived=True, include_legacy_json=True):
        with self._connect() as conn:
            conversation_rows = conn.execute(
                """
                SELECT id, title, created_at, updated_at, status, meta
                FROM conversations
                ORDER BY updated_at ASC, created_at ASC
                """
            ).fetchall()
            db_ids = {row["id"] for row in conversation_rows}
            transcripts = []
            for row in conversation_rows:
                meta = self._parse_json_dict(row["meta"])
                if not include_archived and meta.get("archived"):
                    continue
                message_rows = conn.execute(
                    """
                    SELECT id, role, content, tool_calls, reasoning_content, content_parts, meta,
                           result_obj,
                           token_count, tool_call_id, position, created_at
                    FROM messages
                    WHERE conversation_id = ?
                    ORDER BY position ASC
                    """,
                    (row["id"],),
                ).fetchall()
                messages = []
                for msg_row in message_rows:
                    message = {
                        "id": msg_row["id"],
                        "role": msg_row["role"],
                        "content": msg_row["content"] or "",
                        "position": msg_row["position"],
                        "created_at": msg_row["created_at"],
                        "created_at_iso": self._timestamp_to_iso(msg_row["created_at"]),
                    }
                    if msg_row["tool_calls"]:
                        try:
                            message["tool_calls"] = json.loads(msg_row["tool_calls"])
                        except Exception:
                            message["tool_calls"] = msg_row["tool_calls"]
                    if msg_row["reasoning_content"]:
                        message["reasoning_content"] = msg_row["reasoning_content"]
                    content_parts = self._parse_json_value(msg_row["content_parts"])
                    if isinstance(content_parts, list):
                        message["content_parts"] = content_parts
                    meta_value = self._parse_json_value(msg_row["meta"], default={})
                    if isinstance(meta_value, dict) and meta_value:
                        message["meta"] = meta_value
                    result_obj = self._parse_json_value(msg_row["result_obj"])
                    if result_obj is not None:
                        message["result_obj"] = result_obj
                    if msg_row["token_count"] is not None:
                        message["token_count"] = msg_row["token_count"]
                    if msg_row["tool_call_id"]:
                        message["tool_call_id"] = msg_row["tool_call_id"]
                    messages.append(message)
                transcripts.append(
                    {
                        "id": row["id"],
                        "title": row["title"] or "新任务",
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "created_at_iso": self._timestamp_to_iso(row["created_at"]),
                        "updated_at_iso": self._timestamp_to_iso(row["updated_at"]),
                        "status": row["status"] or "active",
                        "meta": meta,
                        "source": "sqlite",
                        "messages": messages,
                    }
                )

        if include_legacy_json:
            transcripts.extend(self._legacy_json_transcripts(db_ids))
        return transcripts

    def _timestamp_to_iso(self, value):
        if not value:
            return None
        try:
            return datetime.fromtimestamp(int(value)).isoformat()
        except Exception:
            return None

    def _legacy_json_transcripts(self, existing_ids):
        history_dir = os.path.dirname(self.db_path)
        if not os.path.isdir(history_dir):
            return []
        transcripts = []
        for filename in sorted(os.listdir(history_dir)):
            if not (filename.startswith("chat_history_") and filename.endswith(".json")):
                continue
            session_id = filename[len("chat_history_") : -len(".json")]
            if session_id in existing_ids:
                continue
            path = os.path.join(history_dir, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw_messages = json.load(f)
            except Exception:
                continue
            if not isinstance(raw_messages, list):
                logger.error(
                    "conversation_history_structure_invalid",
                    extra={
                        "conversation_id": session_id,
                        "source": "legacy_json",
                        "reason": "messages must be a list",
                    },
                )
                transcripts.append(
                    {
                        "id": session_id,
                        "title": "历史会话损坏",
                        "created_at": None,
                        "updated_at": None,
                        "created_at_iso": None,
                        "updated_at_iso": None,
                        "status": "error",
                        "meta": {"history_load_error": "messages 必须是数组"},
                        "source": "legacy_json",
                        "messages": [],
                    }
                )
                continue
            if not raw_messages:
                continue
            messages = []
            try:
                normalized_messages = self.normalize_messages(
                    raw_messages,
                    conversation_id=session_id,
                )
            except Exception as exc:
                logger.error(
                    "conversation_history_structure_invalid",
                    extra={
                        "conversation_id": session_id,
                        "source": "legacy_json",
                        "reason": str(exc),
                    },
                )
                transcripts.append(
                    {
                        "id": session_id,
                        "title": "历史会话损坏",
                        "created_at": None,
                        "updated_at": None,
                        "created_at_iso": None,
                        "updated_at_iso": None,
                        "status": "error",
                        "meta": {"history_load_error": str(exc)},
                        "source": "legacy_json",
                        "messages": [],
                    }
                )
                continue
            for position, raw_msg in enumerate(normalized_messages):
                if not isinstance(raw_msg, dict):
                    continue
                message = {
                    "id": raw_msg.get("id") or f"{session_id}-{position}",
                    "role": raw_msg.get("role") or "unknown",
                    "content": raw_msg.get("content") or "",
                    "position": position,
                    "created_at": raw_msg.get("created_at"),
                    "created_at_iso": self._timestamp_to_iso(raw_msg.get("created_at")),
                }
                if raw_msg.get("tool_calls"):
                    message["tool_calls"] = raw_msg.get("tool_calls")
                reasoning = raw_msg.get("reasoning_content") or raw_msg.get("reasoning")
                if reasoning:
                    message["reasoning_content"] = reasoning
                if isinstance(raw_msg.get("content_parts"), list):
                    message["content_parts"] = raw_msg.get("content_parts")
                if isinstance(raw_msg.get("meta"), dict) and raw_msg.get("meta"):
                    message["meta"] = raw_msg.get("meta")
                if raw_msg.get("result_obj") is not None:
                    message["result_obj"] = raw_msg.get("result_obj")
                if raw_msg.get("token_count") is not None:
                    message["token_count"] = raw_msg.get("token_count")
                if raw_msg.get("tool_call_id"):
                    message["tool_call_id"] = raw_msg.get("tool_call_id")
                messages.append(message)
            try:
                updated_at = int(os.path.getmtime(path))
            except Exception:
                updated_at = None
            transcripts.append(
                {
                    "id": session_id,
                    "title": self._legacy_title(messages),
                    "created_at": updated_at,
                    "updated_at": updated_at,
                    "created_at_iso": self._timestamp_to_iso(updated_at),
                    "updated_at_iso": self._timestamp_to_iso(updated_at),
                    "status": "legacy",
                    "meta": {},
                    "source": "legacy_json",
                    "messages": messages,
                }
            )
        return transcripts

    def migrate_legacy_json_histories(self, remove_source=False):
        history_dir = os.path.dirname(self.db_path)
        if not os.path.isdir(history_dir):
            return 0
        with self._connect() as conn:
            rows = conn.execute("SELECT id FROM conversations").fetchall()
        existing_ids = {row["id"] for row in rows}
        migrated = 0
        for filename in sorted(os.listdir(history_dir)):
            if not (filename.startswith("chat_history_") and filename.endswith(".json")):
                continue
            session_id = filename[len("chat_history_") : -len(".json")]
            if session_id in existing_ids:
                continue
            path = os.path.join(history_dir, filename)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    raw_messages = json.load(handle)
            except Exception:
                continue
            if not isinstance(raw_messages, list) or not raw_messages:
                continue
            try:
                messages = self.normalize_messages(raw_messages, conversation_id=session_id)
            except Exception as exc:
                logger.error(
                    "conversation_history_migration_skipped",
                    extra={
                        "conversation_id": session_id,
                        "reason": str(exc),
                    },
                )
                continue
            if not messages:
                continue
            self.save_conversation(
                session_id,
                messages,
                title=self._legacy_title(messages),
                status="legacy",
                meta={"migrated_from_legacy_json": True},
            )
            existing_ids.add(session_id)
            migrated += 1
            if remove_source:
                try:
                    os.remove(path)
                except OSError:
                    pass
        return migrated

    def _legacy_title(self, messages):
        for message in messages:
            if message.get("role") == "user":
                content = str(message.get("content") or "").strip()
                if content:
                    return content[:30] + ("..." if len(content) > 30 else "")
        return "旧版历史会话"

    def get_messages(self, conversation_id):
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, tool_calls, reasoning_content, content_parts, meta,
                       result_obj, token_count, tool_call_id
                FROM messages
                WHERE conversation_id = ?
                ORDER BY position ASC
                """,
                (conversation_id,),
            ).fetchall()
        messages = []
        for row in rows:
            messages.append(self._message_row_to_dict(row))
        normalized_messages = self.normalize_messages(messages, conversation_id=conversation_id)
        try:
            changed = json.dumps(messages, ensure_ascii=False, sort_keys=True) != json.dumps(
                normalized_messages,
                ensure_ascii=False,
                sort_keys=True,
            )
        except Exception:
            changed = messages != normalized_messages
        if changed:
            self.replace_messages(conversation_id, normalized_messages)
        return normalized_messages

    def delete_conversation(self, conversation_id):
        artifact_paths = []
        with self._connect() as conn:
            artifact_paths = [
                str(row["path"] or "")
                for row in conn.execute(
                    "SELECT path FROM inline_visualizations WHERE conversation_id = ?",
                    (conversation_id,),
                ).fetchall()
            ]
            conn.execute("DELETE FROM im_sessions WHERE conversation_id = ?", (conversation_id,))
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        if artifact_paths:
            from .env_utils import get_app_data_dir

            visualization_root = os.path.normcase(
                os.path.abspath(os.path.join(get_app_data_dir(), "visualizations"))
            )
            for raw_path in artifact_paths:
                candidate = os.path.normcase(os.path.abspath(raw_path))
                try:
                    inside_root = os.path.commonpath([visualization_root, candidate]) == visualization_root
                except ValueError:
                    inside_root = False
                if not inside_root or not os.path.isfile(candidate):
                    continue
                try:
                    os.remove(candidate)
                except OSError:
                    continue

    def register_inline_visualization(self, conversation_id, artifact):
        if not conversation_id or not isinstance(artifact, dict):
            raise ValueError("conversation_id and artifact are required")
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO conversations (
                    id, title, created_at, updated_at, status, meta
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (conversation_id, "新对话", now, now, "active", "{}"),
            )
            conn.execute(
                """
                INSERT INTO inline_visualizations (
                    conversation_id, file, path, sha256, title, origins, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id, file) DO UPDATE SET
                    path = excluded.path,
                    sha256 = excluded.sha256,
                    title = excluded.title,
                    origins = excluded.origins
                """,
                (
                    conversation_id,
                    artifact.get("file"),
                    artifact.get("path"),
                    artifact.get("sha256"),
                    artifact.get("title") or "",
                    json.dumps(artifact.get("origins") or [], ensure_ascii=False),
                    now,
                ),
            )
        return self.get_inline_visualization(conversation_id, artifact.get("file"))

    def get_inline_visualization(self, conversation_id, file):
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT conversation_id, file, path, sha256, title, origins, created_at
                FROM inline_visualizations
                WHERE conversation_id = ? AND file = ?
                """,
                (conversation_id, file),
            ).fetchone()
        if not row:
            return None
        return {
            "conversation_id": row["conversation_id"],
            "file": row["file"],
            "path": row["path"],
            "sha256": row["sha256"],
            "title": row["title"] or "",
            "origins": self._parse_json_value(row["origins"], default=[]),
            "created_at": row["created_at"],
        }

    def get_inline_visualization_state(self, conversation_id, file, sha256=""):
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT sha256, state FROM inline_visualization_states
                WHERE conversation_id = ? AND file = ?
                """,
                (conversation_id, file),
            ).fetchone()
        if not row or (sha256 and row["sha256"] != sha256):
            return {}
        return self._parse_json_value(row["state"], default={}) or {}

    def save_inline_visualization_state(self, conversation_id, file, sha256, state):
        from .inline_visualization import INLINE_VISUALIZATION_STATE_MAX_BYTES

        payload = json.dumps(state if isinstance(state, dict) else {}, ensure_ascii=False)
        if len(payload.encode("utf-8")) > INLINE_VISUALIZATION_STATE_MAX_BYTES:
            raise ValueError("可视化状态超过 64 KB。")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO inline_visualization_states (
                    conversation_id, file, sha256, state, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id, file) DO UPDATE SET
                    sha256 = excluded.sha256,
                    state = excluded.state,
                    updated_at = excluded.updated_at
                """,
                (conversation_id, file, sha256, payload, int(time.time())),
            )

    @staticmethod
    def _normalize_deliverable_location(path):
        text = str(path or "").strip()
        if not text:
            return "", ""
        normalized = os.path.normpath(os.path.abspath(os.path.expanduser(text)))
        return normalized, os.path.normcase(normalized)

    def register_deliverable(self, workspace_path, path, conversation_id=None, source="generated"):
        workspace, workspace_key = self._normalize_deliverable_location(workspace_path)
        deliverable_path, path_key = self._normalize_deliverable_location(path)
        if not workspace or not deliverable_path:
            raise ValueError("workspace_path and path are required")
        try:
            if os.path.commonpath([workspace, deliverable_path]) != workspace:
                raise ValueError("deliverable path must stay inside the workspace")
        except ValueError as exc:
            raise ValueError("deliverable path must stay inside the workspace") from exc
        now = int(time.time())
        deliverable_id = uuid.uuid4().hex
        normalized_source = str(source or "generated").strip() or "generated"
        normalized_conversation_id = str(conversation_id or "").strip() or None
        with self._connect() as conn:
            if normalized_conversation_id:
                conversation_exists = conn.execute(
                    "SELECT 1 FROM conversations WHERE id = ?",
                    (normalized_conversation_id,),
                ).fetchone()
                if not conversation_exists:
                    normalized_conversation_id = None
            existing = conn.execute(
                "SELECT id, created_at FROM deliverables WHERE workspace_key = ? AND path_key = ?",
                (workspace_key, path_key),
            ).fetchone()
            if existing:
                deliverable_id = existing["id"]
                conn.execute(
                    """
                    UPDATE deliverables
                    SET workspace_path = ?, path = ?, conversation_id = COALESCE(?, conversation_id),
                        source = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (workspace, deliverable_path, normalized_conversation_id, normalized_source, now, deliverable_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO deliverables (
                        id, workspace_path, workspace_key, path, path_key,
                        conversation_id, source, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        deliverable_id, workspace, workspace_key, deliverable_path, path_key,
                        normalized_conversation_id, normalized_source, now, now,
                    ),
                )
        return deliverable_id

    def list_deliverables(self, workspace_path, prune_missing=True):
        _workspace, workspace_key = self._normalize_deliverable_location(workspace_path)
        if not workspace_key:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, workspace_path, path, conversation_id, source, created_at, updated_at
                FROM deliverables
                WHERE workspace_key = ?
                ORDER BY updated_at DESC, path COLLATE NOCASE ASC
                """,
                (workspace_key,),
            ).fetchall()
            missing_ids = [row["id"] for row in rows if not os.path.isfile(row["path"])]
            if prune_missing and missing_ids:
                conn.executemany("DELETE FROM deliverables WHERE id = ?", [(item,) for item in missing_ids])
                rows = [row for row in rows if row["id"] not in set(missing_ids)]
        return [dict(row) for row in rows]

    def unregister_deliverable(self, workspace_path, path):
        _workspace, workspace_key = self._normalize_deliverable_location(workspace_path)
        _path, path_key = self._normalize_deliverable_location(path)
        if not workspace_key or not path_key:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM deliverables WHERE workspace_key = ? AND path_key = ?",
                (workspace_key, path_key),
            )
        return bool(cursor.rowcount)

    def is_deliverable(self, workspace_path, path):
        _workspace, workspace_key = self._normalize_deliverable_location(workspace_path)
        _path, path_key = self._normalize_deliverable_location(path)
        if not workspace_key or not path_key:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM deliverables WHERE workspace_key = ? AND path_key = ?",
                (workspace_key, path_key),
            ).fetchone()
        return row is not None

    def has_conversation(self, conversation_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        return row is not None

    def get_im_session(self, provider, im_user_id):
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT conversation_id
                FROM im_sessions
                WHERE provider = ? AND im_user_id = ?
                """,
                (provider, im_user_id),
            ).fetchone()
        return row["conversation_id"] if row else None

    def upsert_im_session(self, provider, im_user_id, conversation_id):
        now = int(time.time())
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT conversation_id
                FROM im_sessions
                WHERE provider = ? AND im_user_id = ?
                """,
                (provider, im_user_id),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE im_sessions
                    SET conversation_id = ?, updated_at = ?
                    WHERE provider = ? AND im_user_id = ?
                    """,
                    (conversation_id, now, provider, im_user_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO im_sessions (
                        provider, im_user_id, conversation_id, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (provider, im_user_id, conversation_id, now, now),
                )

    def get_im_session_binding_by_conversation(self, conversation_id):
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT provider, im_user_id
                FROM im_sessions
                WHERE conversation_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "provider": row["provider"],
            "im_user_id": row["im_user_id"],
        }

    def get_im_daily_summary(self, provider, im_user_id, chat_id, summary_date):
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT provider, im_user_id, chat_id, summary_date, conversation_id, summary_text,
                       source_message_upto_pos, token_estimate, created_at, updated_at
                FROM im_daily_summaries
                WHERE provider = ? AND im_user_id = ? AND chat_id = ? AND summary_date = ?
                """,
                (provider, im_user_id, chat_id, summary_date),
            ).fetchone()
        if not row:
            return None
        return {
            "provider": row["provider"],
            "im_user_id": row["im_user_id"],
            "chat_id": row["chat_id"],
            "summary_date": row["summary_date"],
            "conversation_id": row["conversation_id"],
            "summary_text": row["summary_text"] or "",
            "source_message_upto_pos": row["source_message_upto_pos"],
            "token_estimate": row["token_estimate"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def upsert_im_daily_summary(
        self,
        provider,
        im_user_id,
        chat_id,
        summary_date,
        conversation_id,
        summary_text,
        source_message_upto_pos,
        token_estimate=None,
    ):
        now = int(time.time())
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT provider
                FROM im_daily_summaries
                WHERE provider = ? AND im_user_id = ? AND chat_id = ? AND summary_date = ?
                """,
                (provider, im_user_id, chat_id, summary_date),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE im_daily_summaries
                    SET conversation_id = ?, summary_text = ?, source_message_upto_pos = ?,
                        token_estimate = ?, updated_at = ?
                    WHERE provider = ? AND im_user_id = ? AND chat_id = ? AND summary_date = ?
                    """,
                    (
                        conversation_id,
                        summary_text,
                        source_message_upto_pos,
                        token_estimate,
                        now,
                        provider,
                        im_user_id,
                        chat_id,
                        summary_date,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO im_daily_summaries (
                        provider, im_user_id, chat_id, summary_date, conversation_id, summary_text,
                        source_message_upto_pos, token_estimate, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        provider,
                        im_user_id,
                        chat_id,
                        summary_date,
                        conversation_id,
                        summary_text,
                        source_message_upto_pos,
                        token_estimate,
                        now,
                        now,
                    ),
                )
