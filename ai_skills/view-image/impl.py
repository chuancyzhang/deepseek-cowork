import logging

from core.filesystem_ops import _build_error, _build_ok
from core.tool_images import ToolImageError, persist_tool_image, resolve_tool_image_path


logger = logging.getLogger(__name__)


def view_image(path, detail="auto", workspace_dir=None, _context=None):
    context = _context if isinstance(_context, dict) else {}
    session_id = context.get("session_id") or ""
    call_id = context.get("tool_call_id") or ""
    logger.info("view_image_start session_id=%s call_id=%s", session_id, call_id)
    try:
        if context.get("model_api_protocol") != "responses":
            raise ToolImageError("unsupported_protocol", "view_image 需要使用 Responses 协议的模型。")
        if not context.get("model_supports_vision"):
            raise ToolImageError("vision_not_supported", "当前模型未启用图片输入能力，请选择支持图片输入的 Responses 模型。")
        config = context.get("config_manager")
        if not session_id or config is None:
            raise ToolImageError("session_unavailable", "缺少当前会话的图片存储上下文，无法保存可重放的图片结果。")
        history_dir = config.get_chat_history_dir()
        if not history_dir:
            raise ToolImageError("session_unavailable", "当前会话的图片存储目录不可用。")
        source_path, display_path = resolve_tool_image_path(workspace_dir, path, context)
        part = persist_tool_image(source_path, history_dir, session_id, detail=detail)
    except Exception as exc:
        code = getattr(exc, "code", "image_read_failed")
        logger.warning("view_image_failed session_id=%s call_id=%s code=%s", session_id, call_id, code)
        return _build_error("view_image", code, str(exc), path=path)
    logger.info(
        "view_image_ready session_id=%s call_id=%s mime_type=%s size_bytes=%d",
        session_id, call_id, part["mime_type"], part["size_bytes"],
    )
    return _build_ok("view_image", {
        "path": display_path,
        "content": "图片已读取，随本次工具结果作为图片内容传给模型。",
        "content_parts": [part],
    })


TOOL_EXPORTS = [
    {
        "name": "view_image",
        "handler": view_image,
        "description": "View a local image from the workspace or current conversation attachments. Returns the actual image to the model as multimodal tool output. Requires a vision-capable Responses model; supports PNG, JPEG, WEBP and static GIF up to 20 MiB.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Workspace-relative image path, or the full path of an image attached to the current conversation."},
                "detail": {"type": "string", "enum": ["auto", "low", "high"], "description": "Image input detail level; defaults to auto."},
            },
            "required": ["path"],
        },
        "read_only": True,
        "destructive": False,
        "requires_workspace": False,
        "search_hint": "view_image view read inspect image picture photo screenshot attachment png jpeg webp gif 看图 读取图片 查看截图 图片附件",
    }
]
