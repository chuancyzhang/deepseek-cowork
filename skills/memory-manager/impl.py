import os

from core.memory_store import MemoryStore, memory_project_from_context


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
    workspace_dir = memory_project_from_context(_context or {})
    parts = ["# 全局记忆\n" + (store.read_summary("global").strip() or "（空）")]
    if workspace_dir:
        parts.append("# 当前项目记忆\n" + (store.read_summary("workspace", workspace_dir).strip() or "（空）"))
    else:
        parts.append("# 当前项目记忆\n当前任务未绑定项目。")
    return "\n\n".join(item.strip() for item in parts if item.strip())


def write_memories(content, mode="append", _context=None):
    path = _get_memories_path(_context)
    if not path:
        return "Error: Config manager not available."
    store = MemoryStore(os.path.dirname(path))
    if mode == "replace":
        store.save_summary(content or "")
    elif mode == "append":
        store.append_summary(content)
    else:
        return "Error: mode must be append or replace."
    return "OK"
