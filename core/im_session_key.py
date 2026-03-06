from datetime import datetime


def build_im_session_key(user_id, chat_id, date_key):
    return f"{user_id}:{chat_id}:{date_key}"


def parse_im_session_key(session_key):
    if not isinstance(session_key, str):
        return None
    parts = session_key.rsplit(":", 2)
    if len(parts) != 3:
        return None
    user_id, chat_id, date_key = parts
    if not user_id or not date_key:
        return None
    return {
        "im_user_id": user_id,
        "chat_id": chat_id,
        "summary_date": date_key,
    }


def resolve_date_key(create_time_value, now=None):
    current = now or datetime.now()
    if create_time_value is None:
        return current.strftime("%Y-%m-%d")
    try:
        numeric = int(str(create_time_value).strip())
        if numeric > 10_000_000_000:
            numeric = numeric / 1000.0
        dt = datetime.fromtimestamp(numeric)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return current.strftime("%Y-%m-%d")
