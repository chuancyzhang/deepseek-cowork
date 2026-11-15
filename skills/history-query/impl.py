import json
import os
import sqlite3
from datetime import datetime


def _get_db_path(_context):
    if not _context:
        return None
    config_manager = _context.get("config_manager")
    if not config_manager:
        return None
    history_dir = config_manager.get_chat_history_dir()
    return os.path.join(history_dir, "chat_history.sqlite")


def _parse_date(value):
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        try:
            return int(datetime.strptime(value, "%Y-%m-%d").timestamp())
        except ValueError:
            return None


def _normalize_keywords(keywords):
    if keywords is None:
        return None
    if isinstance(keywords, list):
        parts = [str(k).strip() for k in keywords if str(k).strip()]
    else:
        parts = [p.strip() for p in str(keywords).split(",") if p.strip()]
    if not parts:
        return None
    escaped = [p.replace('"', '""') for p in parts]
    return " OR ".join([f'"{p}"' for p in escaped])


def query_history(keywords=None, start_date=None, end_date=None, limit=10, _context=None):
    db_path = _get_db_path(_context)
    if not db_path or not os.path.exists(db_path):
        return "Error: Chat history database not found."

    start_ts = _parse_date(start_date)
    end_ts = _parse_date(end_date)
    if start_date and start_ts is None:
        return "Error: Invalid start_date format."
    if end_date and end_ts is None:
        return "Error: Invalid end_date format."
    if end_ts is not None:
        end_ts = end_ts + 86399

    try:
        limit = int(limit)
    except Exception:
        limit = 10
    limit = max(1, min(limit, 100))

    keyword_query = _normalize_keywords(keywords)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        fts_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_fts'"
        ).fetchone()

        where_clauses = []
        params = []

        if start_ts is not None:
            where_clauses.append("m.created_at >= ?")
            params.append(start_ts)
        if end_ts is not None:
            where_clauses.append("m.created_at <= ?")
            params.append(end_ts)

        if keyword_query and fts_exists:
            sql = """
                SELECT m.id, m.conversation_id, m.role, m.content, m.reasoning_content, m.created_at, m.position
                FROM messages_fts f
                JOIN messages m ON m.rowid = f.rowid
            """
            where_clauses.insert(0, "messages_fts MATCH ?")
            params = [keyword_query] + params
        else:
            sql = """
                SELECT m.id, m.conversation_id, m.role, m.content, m.reasoning_content, m.created_at, m.position
                FROM messages m
            """
            if keyword_query:
                where_clauses.insert(0, "(m.content LIKE ? OR m.reasoning_content LIKE ?)")
                like_query = f"%{keyword_query.replace('\"', '').replace(' OR ', ' ')}%"
                params = [like_query, like_query] + params

        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)

        sql += " ORDER BY m.created_at DESC, m.position DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()

    results = []
    for row in rows:
        content = row["content"] or ""
        reasoning = row["reasoning_content"] or ""
        results.append(
            {
                "message_id": row["id"],
                "conversation_id": row["conversation_id"],
                "role": row["role"],
                "content": content[:500],
                "reasoning_content": reasoning[:500],
                "created_at": row["created_at"],
                "position": row["position"],
                "created_at_iso": datetime.fromtimestamp(row["created_at"]).isoformat()
                if row["created_at"]
                else None,
            }
        )

    return json.dumps(results, ensure_ascii=False, indent=2)
