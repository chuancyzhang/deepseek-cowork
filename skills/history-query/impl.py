import json
import os
import sqlite3
import time
from datetime import datetime
from core.env_utils import ensure_package_installed


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


def _load_sqlite_vec(conn):
    conn.enable_load_extension(True)
    ensure_package_installed("sqlite-vec", "sqlite_vec")
    import sqlite_vec
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def _ensure_vec_table(conn, embedding_dim):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='messages_vec'"
    ).fetchone()
    if row:
        return
    conn.execute(
        f"CREATE VIRTUAL TABLE messages_vec USING vec0(embedding float[{embedding_dim}], +message_id TEXT, +conversation_id TEXT, +content TEXT, +created_at INTEGER)"
    )


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


def upsert_message_embedding(message_id, embedding=[], conversation_id=None, content=None, created_at=None, _context=None):
    db_path = _get_db_path(_context)
    if not db_path or not os.path.exists(db_path):
        return "Error: Chat history database not found."
    if not embedding:
        return "Error: embedding is required."
    if isinstance(embedding, str):
        try:
            embedding = json.loads(embedding)
        except Exception:
            return "Error: embedding must be a JSON array."
    if not isinstance(embedding, list):
        return "Error: embedding must be a list."

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            _load_sqlite_vec(conn)
            _ensure_vec_table(conn, len(embedding))
            if not created_at:
                created_at = int(time.time())
            conn.execute("DELETE FROM messages_vec WHERE message_id = ?", (message_id,))
            conn.execute(
                "INSERT INTO messages_vec (embedding, message_id, conversation_id, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (json.dumps(embedding), message_id, conversation_id, content, created_at),
            )
    except Exception as e:
        return f"Error: {str(e)}"
    return "OK"


def query_history_vector(embedding=[], limit=10, _context=None):
    db_path = _get_db_path(_context)
    if not db_path or not os.path.exists(db_path):
        return "Error: Chat history database not found."
    if not embedding:
        return "Error: embedding is required."
    if isinstance(embedding, str):
        try:
            embedding = json.loads(embedding)
        except Exception:
            return "Error: embedding must be a JSON array."
    if not isinstance(embedding, list):
        return "Error: embedding must be a list."

    try:
        limit = int(limit)
    except Exception:
        limit = 10
    limit = max(1, min(limit, 100))

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            _load_sqlite_vec(conn)
            _ensure_vec_table(conn, len(embedding))
            rows = conn.execute(
                """
                SELECT rowid, distance, message_id, conversation_id, content, created_at
                FROM messages_vec
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
                """,
                (json.dumps(embedding), limit),
            ).fetchall()
    except Exception as e:
        return f"Error: {str(e)}"

    results = []
    for row in rows:
        results.append(
            {
                "rowid": row["rowid"],
                "distance": row["distance"],
                "message_id": row["message_id"],
                "conversation_id": row["conversation_id"],
                "content": row["content"][:500] if row["content"] else None,
                "created_at": row["created_at"],
                "created_at_iso": datetime.fromtimestamp(row["created_at"]).isoformat()
                if row["created_at"]
                else None,
            }
        )
    return json.dumps(results, ensure_ascii=False, indent=2)
