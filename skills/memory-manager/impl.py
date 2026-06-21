import os

from core.memory_store import MemoryStore


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
    store = MemoryStore(os.path.dirname(path))
    workspace_dir = (_context or {}).get("workspace_dir") or ""
    parts = [store.read_summary("global")]
    if workspace_dir:
        parts.append(store.read_summary("workspace", workspace_dir))
    return "\n\n".join(item.strip() for item in parts if item.strip())


def write_memories(content, mode="append", _context=None):
    path = _get_memories_path(_context)
    if not path:
        return "Error: Config manager not available."
    store = MemoryStore(os.path.dirname(path))
    existing = store.read_summary("global")
    updated = content or "" if mode == "replace" else (existing.rstrip() + "\n" + (content or "")).strip()
    store.save_summary(updated)
    return "OK"
