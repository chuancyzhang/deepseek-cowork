"""Desktop commands and view state beside the existing conversation ledger.

Accepting a submission commits its message and dispatch record together. This
module contains no UI objects and does not change provider-message projection.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import hashlib
import json
import sqlite3
import time

from core.chat_storage import ChatStorage
from core.chat_storage import ConversationWriteConflict
from core.conversation_integrity import canonical_ledger_messages_hash
from core.message_persistence import filter_persistable_messages


class DesktopChatStorage(ChatStorage):
    _read_cursor = ContextVar("desktop_read_cursor", default=None)

    @contextmanager
    def read_scope(self):
        ledger = self.runtime_ledger
        token = self._read_cursor.set(ledger.durable)
        try:
            ledger.barrier(self._read_cursor.get())
            yield self._read_cursor.get()
        finally:
            self._read_cursor.reset(token)

    def upsert_agent(self, agent_id, conversation_id, **values):
        journal = getattr(self, "runtime_journal", None)
        if journal is None:
            return super().upsert_agent(agent_id, conversation_id, **values)
        with journal.session_lock("subagent:" + conversation_id + ":" + agent_id):
            previous = self.get_agent(agent_id) or {}
            now = time.time()
            value = {"id": agent_id, "conversation_id": conversation_id, "parent_message_id": None,
                     "name": "", "status": "queued", "is_subagent": True, "fork_context": False,
                     "created_at": now, "updated_at": now, "started_at": None, "finished_at": None,
                     "last_error": "", "last_result": "", "meta": {}}
            value.update({key: item for key, item in previous.items() if key in value})
            value.update({key: item for key, item in values.items() if key in value and item is not None})
            value["meta"] = json.dumps(value["meta"], ensure_ascii=False)
            journal.stage({"kind": "agent_record", "value": value})
        return {**value, "meta": json.loads(value["meta"])}

    def enqueue_agent_input(self, agent_id, content, message_id, source="human"):
        journal = getattr(self, "runtime_journal", None)
        if journal is None:
            return super().enqueue_agent_input(agent_id, content, message_id, source)
        if not str(content).strip() or not message_id:
            raise ValueError("消息不能为空")
        with journal.session_lock("agent-input:" + agent_id):
            with self._connect() as db:
                previous = db.execute("SELECT * FROM agent_inbox WHERE message_id=?", (message_id,)).fetchone()
                if previous:
                    message = db.execute("SELECT content FROM agent_messages WHERE id=?", (message_id,)).fetchone()
                    if previous["agent_id"] != agent_id or message["content"] != content or previous["source"] != source:
                        raise ValueError("消息 ID 已用于另一条输入")
                    return dict(previous), False
                owner = db.execute("SELECT status FROM agents WHERE id=?", (agent_id,)).fetchone()
                if not owner or owner["status"] in {"closed", "killed"}:
                    raise ValueError("子任务不存在或已关闭")
            journal.stage({"kind": "agent_input", "agent_id": agent_id, "content": content,
                           "message_id": message_id, "source": source})
        return {"message_id": message_id, "agent_id": agent_id, "source": source, "state": "queued"}, True

    def claim_agent_input(self, agent_id, run_id):
        journal = getattr(self, "runtime_journal", None)
        if journal is None:
            return super().claim_agent_input(agent_id, run_id)
        with journal.session_lock("agent-input:" + agent_id):
            rows = self.agent_inbox(agent_id)
            row = next((item for item in rows if item["state"] == "queued"), None)
            if row:
                journal.stage({"kind": "agent_claim", "agent_id": agent_id, "run_id": run_id})
            return row

    def settle_agent_input(self, agent_id, run_id, outcome):
        journal = getattr(self, "runtime_journal", None)
        if journal is None:
            return super().settle_agent_input(agent_id, run_id, outcome)
        journal.stage({"kind": "agent_settle", "agent_id": agent_id, "run_id": run_id, "outcome": outcome})

    def merge_agent_messages(self, agent_id, messages, run_id=""):
        journal = getattr(self, "runtime_journal", None)
        if journal is None:
            return super().merge_agent_messages(agent_id, messages, run_id)
        journal.stage({"kind": "agent_messages", "agent_id": agent_id, "messages": messages, "run_id": run_id})

    def commit_context_window(self, conversation_id, payload):
        journal = getattr(self, "runtime_journal", None)
        if journal is None:
            return super().commit_context_window(conversation_id, payload)
        with journal.session_lock(conversation_id):
            accepted_at = time.time()
            with self._connect() as db:
                result = self._commit_context_window(db, conversation_id, payload, now=accepted_at, apply=False)
            if result["outcome"] != "unchanged":
                journal.stage({"kind": "context_window", "conversation_id": conversation_id,
                               "payload": payload, "accepted_at": accepted_at})
            return result

    def save_conversation_safely(self, conversation_id, messages, title=None, status="active", meta=None):
        if not hasattr(self, "runtime_journal"):
            return super().save_conversation_safely(conversation_id, messages, title, status, meta)
        with self.runtime_journal.session_lock(conversation_id):
            normalized = self.normalize_messages(messages, conversation_id=conversation_id)
            with self._connect() as db:
                normalized = self._remap_cross_conversation_message_ids(db, conversation_id, normalized)
                existing = self.normalize_messages(self.get_messages(conversation_id), conversation_id=conversation_id)
            filtered = filter_persistable_messages(existing)
            if not (self._messages_are_prefix(filtered, normalized) or self._messages_are_prefix(normalized, filtered)):
                raise ConversationWriteConflict(f"conversation {conversation_id} diverged from committed history")
            if len(normalized) < len(filtered):
                return {"outcome": "stale", "message_count": len(filtered)}
            removed = [m["id"] for m in existing if m["id"] not in {item["id"] for item in filtered}]
            op = {"kind": "messages", "conversation_id": conversation_id, "mode": "append", "start": len(filtered),
                  "messages": normalized[len(filtered):], "removed": removed,
                  "retained_ids": [m["id"] for m in filtered] if removed else [],
                  "title": title, "status": status, "meta": meta, "created_at": int(time.time())}
            self.runtime_journal._transaction.operations.append(op)
        return {"outcome": "appended" if len(normalized) > len(filtered) else "unchanged", "message_count": len(normalized)}

    def rewrite_conversation_safely(self, conversation_id, messages, *, expected_messages_hash, expected_revision,
                                    title=None, status="active", meta=None):
        if not hasattr(self, "runtime_journal"):
            return super().rewrite_conversation_safely(conversation_id, messages, expected_messages_hash=expected_messages_hash,
                expected_revision=expected_revision, title=title, status=status, meta=meta)
        with self.runtime_journal.session_lock(conversation_id):
            current = self.get_conversation_record(conversation_id)
            before = self.normalize_messages(self.get_messages(conversation_id), conversation_id=conversation_id)
            if (not current or canonical_ledger_messages_hash(before) != expected_messages_hash
                    or int(current["meta"].get("history_save_revision") or 0) != expected_revision):
                raise ConversationWriteConflict("对话已发生变化，当前编辑内容已保留。")
            normalized = self.normalize_messages(messages, conversation_id=conversation_id)
            with self._connect() as db:
                normalized = self._remap_cross_conversation_message_ids(db, conversation_id, normalized)
            old_dates = {m["id"]: m.get("created_at") for m in before}
            for message in normalized:
                if old_dates.get(message["id"]) is not None:
                    message["created_at"] = old_dates[message["id"]]
            next_meta = {**(current["meta"] if meta is None else meta), "history_save_revision": expected_revision + 1}
            self.runtime_journal._transaction.operations.append({"kind": "messages", "conversation_id": conversation_id,
                "mode": "rewrite", "messages": normalized, "start": 0, "title": title, "status": status,
                "meta": next_meta, "created_at": int(time.time())})
        return {"outcome": "rewritten", "previous_messages_hash": expected_messages_hash,
                "messages_hash": canonical_ledger_messages_hash(normalized), "message_count": len(normalized),
                "revision": expected_revision + 1, "meta": next_meta}

    @contextmanager
    def _connect(self):
        ledger = getattr(self, "runtime_ledger", None)
        if ledger is not None:
            ledger.barrier(self._read_cursor.get())
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
        journal = getattr(self.storage, "runtime_journal", None)
        with journal.session_lock(payload["conversation_id"]) if journal else nullcontext():
            return self._accept(payload, journal)

    def _accept(self, payload, journal):
        conversation_id, run_id = payload["conversation_id"], payload["request_id"]
        message_id = payload["message_id"]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(raw.encode()).hexdigest()
        now = time.time_ns()
        with self.storage._connect() as db:
            if journal is None:
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
            rewrite = payload.get("rewrite")
            retained, undo, meta = None, None, None
            if rewrite:
                from core.conversation_integrity import canonical_ledger_messages_hash
                full_record = db.execute("SELECT title,status,meta FROM conversations WHERE id=?", (conversation_id,)).fetchone()
                meta = json.loads(full_record["meta"] or "{}")
                messages = [self.storage._message_row_to_dict(row) for row in db.execute(
                    "SELECT * FROM messages WHERE conversation_id=? ORDER BY position", (conversation_id,))]
                if canonical_ledger_messages_hash(messages) != rewrite.get("hash") or int(meta.get("history_save_revision") or 0) != rewrite.get("revision"):
                    raise ValueError("对话已发生变化，请重新核对重新生成的范围。输入内容已保留。")
                index = next((i for i, message in enumerate(messages) if message["id"] == rewrite.get("message_id")), -1)
                if index < 0 or messages[index]["role"] != "user" or (messages[index].get("meta") or {}).get("same_turn_guidance"):
                    raise ValueError("请选择一轮完整的用户请求重新生成。")
                undo = {"conversation_id": conversation_id, "messages": messages, "meta": meta, "title": full_record["title"], "status": full_record["status"]}
                retained = messages[:index]
                meta = {**meta, "history_save_revision": int(meta.get("history_save_revision") or 0) + 1}
            position = len(retained) if retained is not None else db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM messages WHERE conversation_id=?", (conversation_id,)).fetchone()[0]
            message = {"id": message_id, "role": "user", "content": payload["content"],
                       "content_parts": payload.get("content_parts"),
                       "created_at": int(time.time()), "meta": {**(payload.get("message_meta") or {}), "turn_id": run_id, "request_id": run_id,
                       "sequence": position, "desktop_attachments": payload.get("attachments", [])}}
            if rewrite:
                undo.update(expected_hash=canonical_ledger_messages_hash(retained + [message]), expected_revision=meta["history_save_revision"])
            run = {"id": run_id, "conversation_id": conversation_id, "message_id": message_id, "fingerprint": fingerprint,
                   "status": "queued", "payload": raw, "created_at": now, "updated_at": now, "error": ""}
            operation = {"kind": "submission", "run": run, "message": message, "position": position,
                         "title": payload["display_text"][:32] or "附件任务", "retained": retained, "meta": meta,
                         "undo": undo, "draft_revision": payload.get("draft_revision")}
            if journal:
                journal.stage(operation)
            else:
                self.apply_submission(db, self.storage, operation)
        return run, True

    @staticmethod
    def apply_submission(db, storage, operation):
        run, message = operation["run"], operation["message"]
        cid = run["conversation_id"]
        if operation["retained"] is not None:
            storage._invalidate_context_for_rewrite(db, cid, operation["retained"])
            storage._replace_messages_in_connection(db, cid, operation["retained"])
            db.execute("UPDATE conversations SET meta=? WHERE id=?", (json.dumps(operation["meta"], ensure_ascii=False), cid))
        storage._insert_message_row(db, "messages", "conversation_id", cid, message, operation["position"], message["created_at"])
        db.execute("UPDATE conversations SET updated_at=?,title=CASE WHEN title='新对话' THEN ? ELSE title END WHERE id=?",
                   (message["created_at"], operation["title"], cid))
        db.execute("INSERT INTO desktop_runs(id,conversation_id,message_id,fingerprint,status,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                   tuple(run[key] for key in ("id", "conversation_id", "message_id", "fingerprint", "status", "payload", "created_at", "updated_at")))
        if operation["draft_revision"] is not None:
            db.execute("UPDATE desktop_view_state SET value=?,revision=revision+1 WHERE key=? AND revision=?",
                       (json.dumps({"text": "", "attachments": []}), "draft:" + cid, operation["draft_revision"]))
        if operation["undo"]:
            db.execute("INSERT INTO desktop_view_state(key,value) VALUES(?,?)", ("history_undo:" + run["id"], json.dumps(operation["undo"], ensure_ascii=False)))

    def get_run(self, run_id):
        with self.storage._connect() as db:
            row = db.execute("SELECT * FROM desktop_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def latest_run(self, conversation_id):
        with self.storage._connect() as db:
            row = db.execute("SELECT r.* FROM desktop_runs r JOIN messages m ON m.id=r.message_id "
                             "WHERE r.conversation_id=? AND m.conversation_id=r.conversation_id ORDER BY r.created_at DESC LIMIT 1", (conversation_id,)).fetchone()
        return dict(row) if row else None

    def conversation_runs(self, conversation_id):
        with self.storage._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT r.* FROM desktop_runs r JOIN messages m ON m.id=r.message_id "
                "WHERE r.conversation_id=? AND m.conversation_id=r.conversation_id ORDER BY r.created_at", (conversation_id,))]

    def set_run(self, run_id, status, error=""):
        journal = getattr(self.storage, "runtime_journal", None)
        if journal:
            journal.stage({"kind": "run_status", "run_id": run_id, "status": status, "error": str(error), "updated_at": time.time_ns()})
            return
        with self.storage._connect() as db:
            db.execute("UPDATE desktop_runs SET status=?,error=?,updated_at=? WHERE id=?", (status, str(error), time.time_ns(), run_id))

    def active_runs(self):
        with self.storage._connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM desktop_runs WHERE status IN ('queued','running','stopping')")]

    def get_state(self, key, default=None):
        return self.versioned_state(key, default)["value"]

    def versioned_state(self, key, default=None):
        with self.storage._connect() as db:
            row = db.execute("SELECT value,revision FROM desktop_view_state WHERE key=?", (key,)).fetchone()
        return {"value": json.loads(row[0]) if row else default, "revision": row[1] if row else -1}

    def put_state(self, key, value, expected_revision=None):
        journal = getattr(self.storage, "runtime_journal", None)
        if journal and key.startswith("history_undo:"):
            journal.stage({"kind": "history_undo", "key": key, "value": value})
            return {"saved": True}
        with self.storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT revision FROM desktop_view_state WHERE key=?", (key,)).fetchone()
            revision = row[0] if row else -1
            if expected_revision is not None and revision != expected_revision:
                raise ValueError("草稿已在另一处更新，当前输入仍保留。请重新载入后核对，避免覆盖新内容。")
            db.execute("INSERT INTO desktop_view_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, revision=revision+1",
                       (key, json.dumps(value, ensure_ascii=False)))
        return {"saved": True, "revision": revision + 1}

    def message_window(self, conversation_id, *, before=None, limit=80):
        limit = max(1, min(int(limit), 200))
        with self.storage._connect() as db:
            params = [conversation_id]
            condition = ""
            if before is not None:
                condition = " AND position < ?"
                params.append(int(before))
            rows = db.execute("SELECT * FROM messages WHERE conversation_id=?" + condition + " ORDER BY position DESC LIMIT ?", (*params, limit + 1)).fetchall()
            count = db.execute("SELECT count(*) FROM messages WHERE conversation_id=?", (conversation_id,)).fetchone()[0]
        has_more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
        return {"messages": [self.storage._message_row_to_dict(row) for row in rows], "total": count,
                "before": rows[0]["position"] if rows and has_more else None, "has_more": has_more,
                "ledger_start": rows[0]["position"] if rows else 0, "message_positions": {row["id"]: row["position"] for row in rows}}

    def summarize_conversations(self, records):
        """Project existing draft/view data without rewriting history or activity dates."""
        from core.conversation_drafts import history_record_has_content, history_record_title
        keys = [prefix + row["id"] for row in records for prefix in ("draft:", "view:")]
        states = {}
        with self.storage._connect() as db:
            for start in range(0, len(keys), 400):
                batch = keys[start:start + 400]
                states.update((row["key"], json.loads(row["value"])) for row in db.execute(
                    "SELECT key,value FROM desktop_view_state WHERE key IN (" + ",".join("?" for _ in batch) + ")", batch))
        result = []
        for record in records:
            meta = dict(record.get("meta") or {})
            cid = record["id"]
            draft = states.get("draft:" + cid)
            view = states.get("view:" + cid) or {}
            if draft is not None:
                meta["composer_draft"] = {"text": draft.get("text", ""), "files": draft.get("attachments", [])}
            if "selected_skill_names" not in meta:
                meta["selected_skill_names"] = view.get("selectedSkills", [])
            if any(item.get("text") != item.get("saved") for item in view.get("editors", [])):
                meta["has_file_changes"] = True
            projected = {**record, "meta": meta}
            result.append({**record, "has_content": history_record_has_content(projected),
                           "display_title": history_record_title(projected)})
        return result
