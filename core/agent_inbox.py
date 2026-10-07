"""Transactional child input admission; presentation order is independent of execution order."""
import time


class AgentInboxStorage:
    def _ensure_agent_inbox(self):
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS agent_inbox (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT NOT NULL UNIQUE,
                agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                source TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                run_id TEXT, created_at REAL NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_inbox_owner ON agent_inbox(agent_id, state, sequence)")
            conn.execute("""CREATE TABLE IF NOT EXISTS agent_message_runs (
                message_id TEXT PRIMARY KEY REFERENCES agent_messages(id) ON DELETE CASCADE,
                agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
                run_id TEXT NOT NULL, ordinal INTEGER NOT NULL)""")

    def enqueue_agent_input(self, agent_id, content, message_id, source="human"):
        if not str(content).strip() or not message_id:
            raise ValueError("消息不能为空")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT * FROM agent_inbox WHERE message_id=?", (message_id,)).fetchone()
            if previous:
                message = conn.execute("SELECT content FROM agent_messages WHERE id=?", (message_id,)).fetchone()
                if previous["agent_id"] != agent_id or message["content"] != content or previous["source"] != source:
                    raise ValueError("消息 ID 已用于另一条输入")
                return dict(previous), False
            owner = conn.execute("SELECT status FROM agents WHERE id=?", (agent_id,)).fetchone()
            if not owner or owner["status"] in {"closed", "killed"}:
                raise ValueError("子任务不存在或已关闭")
            position = conn.execute("SELECT COALESCE(MAX(position),-1)+1 FROM agent_messages WHERE agent_id=?", (agent_id,)).fetchone()[0]
            now = time.time()
            self._insert_message_row(conn, "agent_messages", "agent_id", agent_id,
                                     {"id": message_id, "role": "user", "content": content, "created_at": now}, position, now)
            conn.execute("INSERT INTO agent_inbox(message_id,agent_id,source,created_at) VALUES(?,?,?,?)", (message_id, agent_id, source, now))
            row = conn.execute("SELECT * FROM agent_inbox WHERE message_id=?", (message_id,)).fetchone()
            return dict(row), True

    def agent_inbox(self, agent_id):
        with self._connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM agent_inbox WHERE agent_id=? ORDER BY sequence", (agent_id,))]

    def claim_agent_input(self, agent_id, run_id):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM agent_inbox WHERE agent_id=? AND state='queued' ORDER BY sequence LIMIT 1", (agent_id,)).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE agent_inbox SET state='claimed',run_id=? WHERE message_id=?", (run_id, row["message_id"]))
            return dict(row)

    def settle_agent_input(self, agent_id, run_id, outcome):
        with self._connect() as conn:
            conn.execute("UPDATE agent_inbox SET state=? WHERE agent_id=? AND run_id=? AND state='claimed'", (outcome, agent_id, run_id))

    def merge_agent_messages(self, agent_id, messages, run_id=""):
        """Update only named messages, preserving positions and concurrently admitted inputs."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            position = conn.execute("SELECT COALESCE(MAX(position),-1)+1 FROM agent_messages WHERE agent_id=?", (agent_id,)).fetchone()[0]
            for ordinal, original in enumerate(messages):
                msg = dict(original)
                existing = conn.execute("SELECT agent_id,position,created_at FROM agent_messages WHERE id=?", (msg["id"],)).fetchone()
                if existing and existing["agent_id"] != agent_id:
                    raise ValueError("子任务消息身份冲突")
                if existing:
                    index = existing["position"]
                    msg["created_at"] = existing["created_at"]
                    # UPDATE through the shared serializer, within this transaction.
                    conn.execute("DELETE FROM agent_messages WHERE id=?", (msg["id"],))
                else:
                    index = position
                    position += 1
                self._insert_message_row(conn, "agent_messages", "agent_id", agent_id, msg, index, time.time())
                if run_id:
                    conn.execute("INSERT OR REPLACE INTO agent_message_runs VALUES(?,?,?,?)", (msg["id"], agent_id, run_id, ordinal))

    def agent_execution_messages(self, agent_id):
        """Rebuild completed turns without moving queued user messages in the visible ledger."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM agent_messages WHERE agent_id=? ORDER BY position", (agent_id,)).fetchall()
            inbox = conn.execute("SELECT * FROM agent_inbox WHERE agent_id=? ORDER BY sequence", (agent_id,)).fetchall()
            runs = conn.execute("SELECT * FROM agent_message_runs WHERE agent_id=? ORDER BY ordinal", (agent_id,)).fetchall()
        messages = {row["id"]: self._message_row_to_dict(row) for row in rows}
        owned = {row["message_id"] for row in inbox} | {row["message_id"] for row in runs}
        result = [messages[row["id"]] for row in rows if row["id"] not in owned]
        for entry in inbox:
            if entry["state"] == "queued":
                continue
            result.append(messages[entry["message_id"]])
            result.extend(messages[row["message_id"]] for row in runs if row["run_id"] == entry["run_id"])
        return [m for m in result if m.get("role") != "assistant" or m.get("content") or m.get("tool_calls") or m.get("reasoning_content")]

    def agent_history_page(self, agent_id, before=None, limit=50):
        limit = min(200, max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM agent_messages WHERE agent_id=? AND position<? ORDER BY position DESC LIMIT ?", (agent_id, int(before) if before is not None else 9223372036854775807, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
        return {"messages": [self._message_row_to_dict(row) for row in rows],
                "before": rows[0]["position"] if more else None}
