import json
import os
import sqlite3
import time
import uuid

AGENT_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "closed",
    "killed",
    "failed_recovered",
}


class ChatStorage:
    def __init__(self, db_path):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._ensure_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

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

    def _parse_json_dict(self, text):
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

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

    def _message_signature(self, message):
        if not isinstance(message, dict):
            return None
        signature = {
            "role": message.get("role") or "",
            "content": message.get("content") or "",
            "tool_call_id": message.get("tool_call_id") or "",
            "reasoning_content": message.get("reasoning_content") or message.get("reasoning") or "",
        }
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

    def normalize_messages(self, messages):
        if not isinstance(messages, list):
            return []

        filtered = []
        seen_ids = set()
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            msg_copy = dict(msg)
            msg_id = msg_copy.get("id")
            if msg_id:
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)
            filtered.append(msg_copy)

        changed = True
        normalized = filtered
        while changed:
            changed = False

            deduped = []
            for msg in normalized:
                role = msg.get("role")
                if deduped:
                    prev = deduped[-1]
                    if (
                        role == "user"
                        and prev.get("role") == "user"
                        and (msg.get("content") or "") == (prev.get("content") or "")
                    ):
                        changed = True
                        continue
                    if role != "user":
                        if self._message_signature(msg) == self._message_signature(prev):
                            changed = True
                            continue
                deduped.append(msg)
            normalized = deduped

            collapsed = []
            i = 0
            while i < len(normalized):
                matched = False
                max_block = (len(normalized) - i) // 2
                for block_size in range(max_block, 0, -1):
                    left = normalized[i:i + block_size]
                    right = normalized[i + block_size:i + (2 * block_size)]
                    if not left or not right:
                        continue
                    roles = {msg.get("role") for msg in left + right}
                    if "user" in roles:
                        continue
                    left_signatures = [self._message_signature(msg) for msg in left]
                    right_signatures = [self._message_signature(msg) for msg in right]
                    if left_signatures == right_signatures:
                        collapsed.extend(left)
                        i += block_size * 2
                        changed = True
                        matched = True
                        break
                if matched:
                    continue
                collapsed.append(normalized[i])
                i += 1
            normalized = collapsed

        return normalized

    def upsert_conversation(self, conversation_id, title=None, status="active", meta=None):
        now = int(time.time())
        meta_json = json.dumps(meta, ensure_ascii=False) if meta is not None else None
        with self._connect() as conn:
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
        normalized_messages = self.normalize_messages(messages)
        now = int(time.time())
        with self._connect() as conn:
            conn.execute("DELETE FROM agent_messages WHERE agent_id = ?", (agent_id,))
            for index, msg in enumerate(normalized_messages):
                msg_id = msg.get("id") or uuid.uuid4().hex
                tool_calls = msg.get("tool_calls")
                tool_calls_json = (
                    json.dumps(tool_calls, ensure_ascii=False) if tool_calls is not None else None
                )
                reasoning_content = msg.get("reasoning_content") or msg.get("reasoning")
                conn.execute(
                    """
                    INSERT INTO agent_messages (
                        id, agent_id, role, content, tool_calls, reasoning_content,
                        token_count, tool_call_id, position, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        msg_id,
                        agent_id,
                        msg.get("role"),
                        msg.get("content"),
                        tool_calls_json,
                        reasoning_content,
                        msg.get("token_count"),
                        msg.get("tool_call_id"),
                        index,
                        msg.get("created_at") or now,
                    ),
                )

    def get_agent_messages(self, agent_id):
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, tool_calls, reasoning_content, token_count, tool_call_id
                FROM agent_messages
                WHERE agent_id = ?
                ORDER BY position ASC
                """,
                (agent_id,),
            ).fetchall()
        messages = []
        for row in rows:
            msg = {"id": row["id"], "role": row["role"], "content": row["content"]}
            if row["tool_calls"]:
                msg["tool_calls"] = json.loads(row["tool_calls"])
            if row["reasoning_content"] is not None:
                msg["reasoning_content"] = row["reasoning_content"]
                msg["reasoning"] = row["reasoning_content"]
            if row["token_count"] is not None:
                msg["token_count"] = row["token_count"]
            if row["tool_call_id"] is not None:
                msg["tool_call_id"] = row["tool_call_id"]
            messages.append(msg)
        normalized_messages = self.normalize_messages(messages)
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
        normalized_messages = self.normalize_messages(messages)
        now = int(time.time())
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            for index, msg in enumerate(normalized_messages):
                msg_id = msg.get("id") or uuid.uuid4().hex
                tool_calls = msg.get("tool_calls")
                tool_calls_json = (
                    json.dumps(tool_calls, ensure_ascii=False) if tool_calls is not None else None
                )
                reasoning_content = msg.get("reasoning_content") or msg.get("reasoning")
                conn.execute(
                    """
                    INSERT INTO messages (
                        id, conversation_id, role, content, tool_calls, reasoning_content,
                        token_count, tool_call_id, position, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        msg_id,
                        conversation_id,
                        msg.get("role"),
                        msg.get("content"),
                        tool_calls_json,
                        reasoning_content,
                        msg.get("token_count"),
                        msg.get("tool_call_id"),
                        index,
                        msg.get("created_at") or now,
                    ),
                )

    def save_conversation(self, conversation_id, messages, title=None, status="active", meta=None):
        self.upsert_conversation(conversation_id, title=title, status=status, meta=meta)
        self.replace_messages(conversation_id, self.normalize_messages(messages))

    def list_conversations(self):
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT c.id, c.title, c.updated_at, c.status, c.meta, im.provider AS im_provider
                FROM conversations c
                LEFT JOIN (
                    SELECT conversation_id, MIN(provider) AS provider
                    FROM im_sessions
                    GROUP BY conversation_id
                ) im ON im.conversation_id = c.id
                ORDER BY c.updated_at DESC
                """
            ).fetchall()
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

    def get_messages(self, conversation_id):
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, role, content, tool_calls, reasoning_content, token_count, tool_call_id
                FROM messages
                WHERE conversation_id = ?
                ORDER BY position ASC
                """,
                (conversation_id,),
            ).fetchall()
        messages = []
        for row in rows:
            msg = {"id": row["id"], "role": row["role"], "content": row["content"]}
            if row["tool_calls"]:
                msg["tool_calls"] = json.loads(row["tool_calls"])
            if row["reasoning_content"] is not None:
                msg["reasoning_content"] = row["reasoning_content"]
                msg["reasoning"] = row["reasoning_content"]
            if row["token_count"] is not None:
                msg["token_count"] = row["token_count"]
            if row["tool_call_id"] is not None:
                msg["tool_call_id"] = row["tool_call_id"]
            messages.append(msg)
        normalized_messages = self.normalize_messages(messages)
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
        with self._connect() as conn:
            conn.execute("DELETE FROM im_sessions WHERE conversation_id = ?", (conversation_id,))
            conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))

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
