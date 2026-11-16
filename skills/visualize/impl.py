import json
import os
import logging

from core.inline_visualization import (
    is_visualize_enabled_context,
    publish_visualization_fragment,
    visualization_staging_dir,
)

logger = logging.getLogger(__name__)


def _conversation_id(context):
    if not isinstance(context, dict):
        return ""
    return str(context.get("conversation_id") or context.get("session_id") or "").strip()


def _disabled_error():
    return {
        "ok": False,
        "status": "denied",
        "content": "交互可视化技能未加载，未执行任何可视化代码。",
    }


def run_visualization_python(workspace_dir, code, _context=None):
    """Run Python with a conversation-scoped visualization output directory."""
    if not is_visualize_enabled_context(_context):
        return _disabled_error()
    conversation_id = _conversation_id(_context)
    if not conversation_id:
        return {"ok": False, "status": "error", "content": "缺少当前会话 ID，未执行可视化代码。"}
    manager = (_context or {}).get("skill_manager")
    runner = getattr(manager, "tools", {}).get("run_python_code") if manager is not None else None
    if not callable(runner):
        return {
            "ok": False,
            "status": "error",
            "content": "Python Runner 未加载，无法生成交互可视化。",
        }
    logger.info("visualization_python_start conversation_id=%s", conversation_id)
    result = runner(workspace_dir=workspace_dir, code=code, _context=_context)
    if isinstance(result, dict):
        logger.info("visualization_python_done conversation_id=%s status=%s", conversation_id, result.get("status"))
        return result
    ok = not str(result or "").startswith("Error")
    logger.info("visualization_python_done conversation_id=%s ok=%s", conversation_id, ok)
    return {
        "ok": ok,
        "status": "completed" if ok else "error",
        "content": str(result or ""),
        "visualization_dir": visualization_staging_dir(conversation_id, create=True),
    }


def finalize_inline_visualization(filename, title="", _context=None):
    """Validate and publish one generated HTML fragment for inline rendering."""
    if not is_visualize_enabled_context(_context):
        return _disabled_error()
    conversation_id = _conversation_id(_context)
    if not conversation_id:
        return {"ok": False, "status": "error", "content": "缺少当前会话 ID。"}
    logger.info("visualization_publish_start conversation_id=%s", conversation_id)
    try:
        artifact = publish_visualization_fragment(
            conversation_id,
            filename,
            title=title,
        )
        storage = (_context or {}).get("chat_storage")
        if storage is None:
            raise RuntimeError("聊天存储未初始化，无法登记内联可视化。")
        storage.register_inline_visualization(conversation_id, artifact)
        logger.info("visualization_publish_done conversation_id=%s sha256=%s bytes=%s", conversation_id, artifact["sha256"], artifact["bytes"])
        return {
            "ok": True,
            "source_tool": "finalize_inline_visualization",
            "content": f"已发布交互可视化：{artifact['file']}",
            "file": artifact["file"],
            "sha256": artifact["sha256"],
            "bytes": artifact["bytes"],
            "directive": artifact["directive"],
        }
    except Exception as exc:
        logger.warning("visualization_publish_failed conversation_id=%s reason=%s", conversation_id, type(exc).__name__)
        return {
            "ok": False,
            "status": "error",
            "content": f"交互可视化发布失败：{exc}",
        }


TOOL_EXPORTS = [
    {
        "name": "run_visualization_python",
        "handler": run_visualization_python,
        "description": "Execute Python for an inline visualization and write HTML fragments to COWORK_VISUALIZATION_DIR.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code that reads workspace data and writes a fragment."}
            },
            "required": ["code"],
        },
        "read_only": False,
        "search_hint": "generate interactive visualization chart data explorer html fragment python",
    },
    {
        "name": "finalize_inline_visualization",
        "handler": finalize_inline_visualization,
        "description": "Validate and publish a generated HTML fragment, returning the inline conversation directive.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Lowercase hyphenated HTML filename in COWORK_VISUALIZATION_DIR."},
                "title": {"type": "string", "description": "Accessible display title."}
            },
            "required": ["filename"],
        },
        "read_only": False,
        "search_hint": "finalize publish inline visualization html fragment directive",
    },
]
