import os


def _get_memories_path(_context):
    if not _context:
        return None
    config_manager = _context.get("config_manager")
    if not config_manager:
        return None
    history_dir = config_manager.get_chat_history_dir()
    return os.path.join(history_dir, "memories.md")


def read_memories(_context=None):
    path = _get_memories_path(_context)
    if not path:
        return "Error: Config manager not available."
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_memories(content, mode="append", _context=None):
    path = _get_memories_path(_context)
    if not path:
        return "Error: Config manager not available."
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if mode == "replace":
        with open(path, "w", encoding="utf-8") as f:
            f.write(content or "")
        return "OK"
    existing = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
    if existing and not existing.endswith("\n"):
        existing += "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(existing + (content or ""))
    return "OK"
