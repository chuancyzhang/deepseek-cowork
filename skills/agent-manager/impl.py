import os

from core.agent_manager import get_agent_manager_registry
from core.chat_storage import ChatStorage


def _ensure_main_agent(_context):
    if isinstance(_context, dict) and _context.get("is_subagent"):
        raise PermissionError("sub-agents cannot manage other agents")


def _resolve_conversation_id(_context):
    if not isinstance(_context, dict):
        return ""
    return str(
        _context.get("conversation_id")
        or _context.get("session_id")
        or ""
    ).strip()


def _resolve_chat_storage(_context):
    if isinstance(_context, dict):
        existing = _context.get("chat_storage")
        if existing:
            return existing
        cfg = _context.get("config_manager")
        if cfg:
            history_dir = cfg.get_chat_history_dir()
            db_path = os.path.join(history_dir, "chat_history.sqlite")
            return ChatStorage(db_path)
    return None


def _resolve_manager(_context):
    _ensure_main_agent(_context)
    if not isinstance(_context, dict):
        raise ValueError("system context is required")
    manager = _context.get("agent_manager")
    conversation_id = _resolve_conversation_id(_context)
    if not conversation_id:
        raise ValueError("conversation_id is required")
    if manager and getattr(manager, "conversation_id", "") == conversation_id:
        return manager
    cfg = _context.get("config_manager")
    if not cfg:
        raise ValueError("config_manager is required")
    storage = _resolve_chat_storage(_context)
    if not storage:
        raise ValueError("chat storage is required")
    registry = get_agent_manager_registry()
    return registry.get_session_manager(
        conversation_id,
        chat_storage=storage,
        config_manager=cfg,
        workspace_dir=_context.get("workspace_dir"),
        step_signal=_context.get("step_signal"),
        agent_state_signal=_context.get("agent_state_signal"),
    )


def _normalize_targets(targets):
    if isinstance(targets, str):
        text = targets.strip()
        return [text] if text else []
    if isinstance(targets, (list, tuple, set)):
        return [str(item).strip() for item in targets if str(item).strip()]
    return []


def spawn_agent(message, name="", fork_context=False, _context=None):
    manager = _resolve_manager(_context)
    result = manager.spawn_agent(
        message=message,
        name=(name or "").strip() or None,
        fork_context=bool(fork_context),
        current_messages_snapshot=(_context or {}).get("current_messages_snapshot"),
        parent_message_id=(_context or {}).get("current_agent_id") or "",
        source_tool_call_id=(_context or {}).get("tool_call_id") or "",
        run_context=(_context or {}).get("run_context"),
    )
    return {
        "status": "spawned",
        "agent_id": result.get("agent_id"),
        "name": result.get("name") or "",
    }


def send_input(target, message, _context=None):
    manager = _resolve_manager(_context)
    result = manager.send_input(target=target, message=message)
    return {
        "status": result.get("status") or "queued",
        "agent_id": result.get("agent_id"),
        "pending_inputs": result.get("pending_inputs", 0),
    }


def wait_agent(targets=None, timeout_ms=30000, return_when="any", _context=None):
    manager = _resolve_manager(_context)
    result = manager.wait_agent(
        targets=_normalize_targets(targets),
        timeout_ms=int(timeout_ms or 0),
        return_when=return_when,
    )
    return {
        "status": "wait_complete",
        "timed_out": bool(result.get("timed_out")),
        "completed": result.get("completed") or [],
        "pending": result.get("pending") or [],
    }


def close_agent(target, force=False, _context=None):
    manager = _resolve_manager(_context)
    result = manager.close_agent(target=target, force=bool(force))
    return {
        "status": "closed",
        "agent": result,
    }


def list_agents(status_filter=None, _context=None):
    manager = _resolve_manager(_context)
    if isinstance(status_filter, str) and status_filter.strip():
        normalized_filter = status_filter.strip()
    elif isinstance(status_filter, (list, tuple, set)):
        normalized_filter = [str(item).strip() for item in status_filter if str(item).strip()]
    else:
        normalized_filter = None
    items = manager.list_agent_summaries(status_filter=normalized_filter)
    return {
        "status": "ok",
        "count": len(items),
        "agents": items,
    }


TOOL_EXPORTS = [
    {
        "name": "spawn_agent",
        "handler": spawn_agent,
        "description": "Spawn a sub-agent in the current conversation.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Task message for the spawned agent."},
                "name": {"type": "string", "description": "Optional unique display name."},
                "fork_context": {"type": "boolean", "description": "Whether to fork current conversation snapshot."},
            },
            "required": ["message"],
        },
    },
    {
        "name": "send_input",
        "handler": send_input,
        "description": "Send another user message to an existing agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Agent id or unique name."},
                "message": {"type": "string", "description": "Message to enqueue for the agent."},
            },
            "required": ["target", "message"],
        },
    },
    {
        "name": "wait_agent",
        "handler": wait_agent,
        "description": "Wait for one or more agents to reach a terminal status.",
        "parameters": {
            "type": "object",
            "properties": {
                "targets": {
                    "type": "array",
                    "description": "List of agent ids/names. Empty means all known agents in conversation.",
                    "items": {"type": "string"},
                },
                "timeout_ms": {"type": "integer", "description": "Wait timeout in milliseconds."},
                "return_when": {"type": "string", "description": "Use 'any' or 'all'."},
            },
            "required": [],
        },
    },
    {
        "name": "close_agent",
        "handler": close_agent,
        "description": "Close an agent and stop accepting follow-up input.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Agent id or unique name."},
                "force": {"type": "boolean", "description": "Force terminate if still running."},
            },
            "required": ["target"],
        },
    },
    {
        "name": "list_agents",
        "handler": list_agents,
        "description": "List persisted agents for the current conversation.",
        "parameters": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "description": "Optional status filter (e.g. running, completed).",
                }
            },
            "required": [],
        },
    },
]
