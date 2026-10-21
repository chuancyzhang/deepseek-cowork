"""Durable substring search for the sidebar, separate from model history tools.

Hex character tokens turn literal Unicode substrings into FTS5 phrases, even
for one/two-character Chinese queries. No second copy of message text is stored.
The backfill cursor and maintenance triggers commit with the message ledger.
"""

import logging
import os
import time
from datetime import datetime


logger = logging.getLogger(__name__)


class SearchCancelled(Exception):
    """A newer sidebar query or application shutdown superseded this search."""


def search_tokens(value):
    return " ".join(format(ord(char), "x") for char in str(value or "").casefold())


def check_cancelled(cancel_check):
    if cancel_check and cancel_check():
        raise SearchCancelled()


def workspace_key(value):
    value = str(value or "").strip()
    return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(value)))) if value else ""


class SidebarSearchStorage:
    def _ensure_sidebar_search_schema(self):
        if getattr(self, "_sidebar_search_schema_ready", False):
            return
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sidebar_search_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cursor INTEGER NOT NULL,
                    upper_bound INTEGER NOT NULL
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO sidebar_search_state
                SELECT 1, 0, COALESCE(MAX(rowid), 0) FROM messages
            """)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS sidebar_message_fts USING fts5(
                    content, reasoning_content, content='', columnsize=0
                )
            """)
            # Old unindexed rows are read later. Newly inserted rows and rows
            # below the committed cursor are maintained immediately, including
            # cascade deletes and rowid reuse after a history rewrite.
            for suffix, event, reference, body in (
                ("ai", "INSERT", "new", """
                    INSERT INTO sidebar_message_fts(rowid, content, reasoning_content)
                    VALUES(new.rowid, sidebar_search_tokens(new.content),
                           sidebar_search_tokens(new.reasoning_content));
                """),
                ("ad", "DELETE", "old", """
                    INSERT INTO sidebar_message_fts(sidebar_message_fts, rowid, content, reasoning_content)
                    VALUES('delete', old.rowid, sidebar_search_tokens(old.content),
                           sidebar_search_tokens(old.reasoning_content));
                """),
                ("au", "UPDATE OF content, reasoning_content", "old", """
                    INSERT INTO sidebar_message_fts(sidebar_message_fts, rowid, content, reasoning_content)
                    VALUES('delete', old.rowid, sidebar_search_tokens(old.content),
                           sidebar_search_tokens(old.reasoning_content));
                    INSERT INTO sidebar_message_fts(rowid, content, reasoning_content)
                    VALUES(new.rowid, sidebar_search_tokens(new.content),
                           sidebar_search_tokens(new.reasoning_content));
                """),
            ):
                conn.execute(f"""
                    CREATE TRIGGER IF NOT EXISTS sidebar_messages_{suffix}
                    AFTER {event} ON messages
                    WHEN {reference}.rowid <= (SELECT cursor FROM sidebar_search_state WHERE id=1)
                      OR {reference}.rowid > (SELECT upper_bound FROM sidebar_search_state WHERE id=1)
                    BEGIN {body} END
                """)
        self._sidebar_search_schema_ready = True

    def prepare_sidebar_search(self, cancel_check=None):
        """Resume a backfill using short transactions, without rewriting history."""
        started = time.perf_counter()
        count = 0
        try:
            check_cancelled(cancel_check)
            self._ensure_sidebar_search_schema()
            while True:
                check_cancelled(cancel_check)
                with self._connect() as conn:
                    state = conn.execute("SELECT cursor, upper_bound FROM sidebar_search_state WHERE id=1").fetchone()
                    if state[0] >= state[1]:
                        break
                    if not count:
                        logger.info("sidebar_search_index_start")
                    conn.execute("BEGIN IMMEDIATE")
                    # Another process may have advanced while we waited.
                    state = conn.execute("SELECT cursor, upper_bound FROM sidebar_search_state WHERE id=1").fetchone()
                    rows = conn.execute("""
                        SELECT rowid, content, reasoning_content FROM messages
                        WHERE rowid > ? AND rowid <= ? ORDER BY rowid LIMIT 64
                    """, tuple(state))
                    batch_started = time.perf_counter()
                    cursor, batch_count = state[0], 0
                    for row in rows:
                        check_cancelled(cancel_check)
                        conn.execute("""
                            INSERT INTO sidebar_message_fts(rowid, content, reasoning_content)
                            VALUES (?, ?, ?)
                        """, (row[0], search_tokens(row[1]), search_tokens(row[2])))
                        cursor, batch_count = row[0], batch_count + 1
                        if time.perf_counter() - batch_started >= 0.05:
                            break
                    if not batch_count:
                        cursor = state[1]
                    conn.execute("UPDATE sidebar_search_state SET cursor=? WHERE id=1", (cursor,))
                count += batch_count
            if count:
                logger.info("sidebar_search_index_done rows=%s elapsed_ms=%s", count,
                            int((time.perf_counter() - started) * 1000))
        except SearchCancelled:
            logger.info("sidebar_search_index_cancelled committed_rows=%s", count)
            raise
        except Exception as exc:
            logger.error("sidebar_search_index_failed error_type=%s", type(exc).__name__)
            raise

    def rebuild_sidebar_search(self, cancel_check=None):
        """Explicit recovery action; reset only the derived sidebar index."""
        check_cancelled(cancel_check)
        self._ensure_sidebar_search_schema()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO sidebar_message_fts(sidebar_message_fts) VALUES('delete-all')")
            conn.execute("""UPDATE sidebar_search_state SET cursor=0,
                            upper_bound=(SELECT COALESCE(MAX(rowid),0) FROM messages) WHERE id=1""")
        logger.info("sidebar_search_index_rebuild_requested")
        self.prepare_sidebar_search(cancel_check)

    def search_conversations(self, query, limit=50, *, cancel_check=None):
        text = str(query or "").strip()
        if not text:
            return []
        started = time.perf_counter()
        self.prepare_sidebar_search(cancel_check)
        limit = max(1, int(limit or 50))
        pattern = "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._connect() as conn:
            if cancel_check:
                conn.set_progress_handler(lambda: int(cancel_check()), 1000)
            check_cancelled(cancel_check)
            titles = conn.execute("""
                SELECT id FROM conversations WHERE title LIKE ? ESCAPE '\\'
                ORDER BY updated_at DESC, id LIMIT ?
            """, (pattern, limit)).fetchall()
            matches = [row[0] for row in titles]
            if len(matches) < limit:
                rows = conn.execute("""
                    SELECT m.conversation_id, MIN(m.position) AS first_position
                    FROM sidebar_message_fts f JOIN messages m ON m.rowid = f.rowid
                    WHERE sidebar_message_fts MATCH ?
                    GROUP BY m.conversation_id
                    ORDER BY first_position, m.conversation_id LIMIT ?
                """, ('"' + search_tokens(text) + '"', limit)).fetchall()
                seen = set(matches)
                matches.extend(row[0] for row in rows if row[0] not in seen)
            check_cancelled(cancel_check)
        result = matches[:limit]
        logger.debug("sidebar_search_done query_length=%s matches=%s elapsed_ms=%s",
                     len(text), len(result), int((time.perf_counter() - started) * 1000))
        return result

    def search_sidebar_conversations(self, query, projects=(), *, cancel_check=None):
        """Read full summaries only for hits; preserve project-name/title matches.

        Metadata is filtered in the worker, including implicit legacy projects.
        No message bodies are fetched by this listing path.
        """
        matched_ids = set(self.search_conversations(query, limit=200, cancel_check=cancel_check))
        needle = query.casefold()
        names = {workspace_key(p.get("path")): p.get("name") for p in projects if p.get("path")}
        hidden = {workspace_key(p.get("path")) for p in projects
                  if p.get("path") and (p.get("archived") or p.get("hidden"))}
        visible_ids = []
        with self._connect() as conn:
            if cancel_check:
                conn.set_progress_handler(lambda: int(cancel_check()), 1000)
            rows = conn.execute("""
                SELECT c.id, c.title, c.meta, c.updated_at, im.provider AS im_provider
                FROM conversations c LEFT JOIN (
                    SELECT conversation_id, MIN(provider) AS provider FROM im_sessions GROUP BY conversation_id
                ) im ON im.conversation_id = c.id
            """)
            for row in rows:
                check_cancelled(cancel_check)
                meta = self._parse_json_dict(row["meta"])
                if meta.get("archived") or meta.get("team_space_required") or meta.get("workspace_source") == "team_space":
                    continue
                path = str(meta.get("workspace_dir") or "").strip()
                if str(meta.get("workspace_source") or "").strip().lower() == "chat":
                    path = ""
                key = workspace_key(path)
                if key and key in hidden:
                    continue
                name = str(names.get(key) or "").strip() or os.path.basename(key.rstrip(os.sep)) or key
                title = str(row["title"] or "")
                if row["im_provider"] == "feishu":
                    title = f"飞书会话 {datetime.fromtimestamp(row['updated_at']).strftime('%Y-%m-%d')}" if row["updated_at"] else "飞书会话"
                if row["id"] in matched_ids or needle in title.casefold() or (name and needle in name.casefold()):
                    visible_ids.append(row["id"])
        check_cancelled(cancel_check)
        return self.list_conversation_summaries(conversation_ids=visible_ids), matched_ids
