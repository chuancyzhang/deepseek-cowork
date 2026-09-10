"""Durable local image parts for multimodal tool results."""

import base64
import hashlib
from io import BytesIO
import json
import logging
import os
import tempfile

from PIL import Image

from .filesystem_ops import resolve_path
from .generated_images import session_attachment_dir


logger = logging.getLogger(__name__)
MAX_TOOL_IMAGE_BYTES = 20 * 1024 * 1024
SUPPORTED_IMAGE_FORMATS = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
    "GIF": ("image/gif", ".gif"),
}
IMAGE_DETAILS = {"auto", "low", "high"}


class ToolImageError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _path_key(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def tool_image_content_parts(result):
    """Project image-bearing model content, keeping UI-only parts in result_obj."""
    parts = result.get("content_parts") if isinstance(result, dict) else None
    if not isinstance(parts, list) or not any(
        isinstance(part, dict) and part.get("type") == "input_image" for part in parts
    ):
        return []
    return [
        dict(part) for part in parts
        if isinstance(part, dict) and part.get("type") in {"text", "input_image"}
    ]


def resolve_tool_image_path(workspace_dir, path, context):
    raw_path = str(path or "").strip()
    if not raw_path:
        raise ToolImageError("invalid_path", "请提供工作区图片路径或当前会话附件的完整路径。")
    if raw_path.startswith(("\\\\", "//")) or "://" in raw_path or raw_path.startswith("data:"):
        raise ToolImageError("invalid_path", "view_image 只读取本地工作区或当前会话附件中的图片。")
    if not os.path.isabs(raw_path) and not workspace_dir:
        raise ToolImageError("workspace_not_selected", "相对路径需要工作区；会话附件请使用完整路径。")
    candidate = os.path.abspath(
        raw_path if os.path.isabs(raw_path) else os.path.join(workspace_dir, raw_path)
    )
    candidate_key = _path_key(candidate)
    # Only files actually present in this conversation are attachment grants.
    # Do not grant access to the shared attachments root or another session.
    for message in context.get("current_messages_snapshot") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        allowed_types = {
            "user": {"input_image", "input_file"},
            "assistant": {"output_image"},
            "tool": {"input_image"},
        }.get(role, set())
        for part in message.get("content_parts") or []:
            if not isinstance(part, dict) or part.get("type") not in allowed_types:
                continue
            attachment_path = str(part.get("path") or "").strip()
            if (
                attachment_path
                and os.path.isabs(attachment_path)
                and candidate_key == _path_key(attachment_path)
            ):
                return candidate, candidate

    # Workspace access stays bounded even when other tools permit god mode.
    resolved, display_path, error = resolve_path(
        workspace_dir, raw_path, context={}, action="view_image", must_exist=True,
    )
    if error:
        cause = error.get("error") or {}
        code = cause.get("code") or "invalid_path"
        message = {
            "path_not_found": "图片路径不存在，请确认路径后重新调用 view_image。",
            "path_outside_workspace": "图片超出当前工作区，且不是当前会话的附件。请将图片加入当前会话后重试。",
            "workspace_not_selected": "当前没有工作区，且指定图片不是当前会话的附件。请先添加图片附件。",
        }.get(code, str(cause.get("message") or "无法读取指定图片。"))
        raise ToolImageError(
            code,
            message,
        )
    return resolved, display_path


def _read_image_bytes(path):
    if not os.path.isfile(path):
        raise ToolImageError("not_a_file", "图片不存在或路径不是普通文件。")
    with open(path, "rb") as handle:
        raw = handle.read(MAX_TOOL_IMAGE_BYTES + 1)
    if not raw:
        raise ToolImageError("empty_image", "图片文件为空。")
    if len(raw) > MAX_TOOL_IMAGE_BYTES:
        raise ToolImageError("image_too_large", "图片超过 20 MiB，请缩小图片后重新调用 view_image。")
    return raw


def persist_tool_image(path, history_dir, session_id, *, detail="auto"):
    if detail not in IMAGE_DETAILS:
        raise ToolImageError("invalid_detail", "detail 必须是 auto、low 或 high。")
    raw = _read_image_bytes(path)
    try:
        with Image.open(BytesIO(raw)) as image:
            image_format = str(image.format or "").upper()
            if image_format not in SUPPORTED_IMAGE_FORMATS:
                raise ToolImageError("unsupported_format", "支持 PNG、JPEG、WEBP 和静态 GIF 图片。")
            if getattr(image, "is_animated", False):
                raise ToolImageError("animated_image", "请先将动画导出为要查看的静态图片，再调用 view_image。")
            width, height = image.size
            image.verify()
        with Image.open(BytesIO(raw)) as image:
            image.load()
    except ToolImageError:
        raise
    except Exception as exc:
        raise ToolImageError("invalid_image", "图片无法解码，请确认文件未损坏且格式受支持。") from exc

    mime_type, extension = SUPPORTED_IMAGE_FORMATS[image_format]
    digest = hashlib.sha256(raw).hexdigest()
    target_dir = os.path.join(session_attachment_dir(history_dir, session_id), "tool-images")
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, digest + extension)
    # Content-addressed snapshots keep replay independent of subsequent edits
    # or deletion of the source. Concurrent reads of the same image are safe.
    temporary_path = ""
    try:
        descriptor, temporary_path = tempfile.mkstemp(prefix=".image-", suffix=".tmp", dir=target_dir)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target_path)
        temporary_path = ""
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)
    return {
        "type": "input_image",
        "path": target_path,
        "name": os.path.basename(path),
        "mime_type": mime_type,
        "sha256": digest,
        "size_bytes": len(raw),
        "width": width,
        "height": height,
        "detail": detail,
    }


def build_responses_tool_output(content, content_parts, *, supports_vision, tool_call_id):
    """Encode image bytes only at the wire boundary; the ledger stores paths."""
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    if not content_parts:
        return text or ""
    output = [{"type": "input_text", "text": text}] if text else []
    image_count = 0
    image_bytes = 0
    try:
        if not isinstance(content_parts, list):
            raise ToolImageError("invalid_content_parts", "工具多模态结果必须使用内容块列表。")
        for part in content_parts:
            if not isinstance(part, dict):
                raise ToolImageError("invalid_content_part", "工具多模态结果包含无效的内容块。")
            if part.get("type") == "text":
                output.append({"type": "input_text", "text": str(part.get("text") or "")})
                continue
            if part.get("type") != "input_image":
                raise ToolImageError("unsupported_content_part", "工具多模态结果包含不支持的内容类型。")
            if not supports_vision:
                raise ToolImageError("vision_not_supported", "历史工具结果包含图片，请切换到支持图片输入的 Responses 模型后继续。")
            mime_type = str(part.get("mime_type") or "")
            if mime_type not in {value[0] for value in SUPPORTED_IMAGE_FORMATS.values()}:
                raise ToolImageError("unsupported_format", "工具图片缺少有效的图片格式。")
            detail = str(part.get("detail") or "auto")
            if detail not in IMAGE_DETAILS:
                raise ToolImageError("invalid_detail", "工具图片的 detail 必须是 auto、low 或 high。")
            raw = _read_image_bytes(str(part.get("path") or ""))
            digest = str(part.get("sha256") or "")
            if not digest or hashlib.sha256(raw).hexdigest() != digest:
                raise ToolImageError("image_snapshot_changed", "已保存的工具图片快照被修改，无法可靠重放。请恢复原快照后继续。")
            output.append({
                "type": "input_image",
                "image_url": f"data:{mime_type};base64,{base64.b64encode(raw).decode('ascii')}",
                "detail": detail,
            })
            image_count += 1
            image_bytes += len(raw)
    except (ToolImageError, OSError) as exc:
        logger.warning(
            "tool_image_payload_failed call_id=%s code=%s",
            tool_call_id, getattr(exc, "code", type(exc).__name__),
        )
        raise ToolImageError(
            getattr(exc, "code", "image_read_failed"),
            f"工具图片未能传给模型（call_id={tool_call_id}）：{exc}",
        ) from exc
    if image_count:
        logger.info(
            "tool_image_payload_prepared call_id=%s image_count=%d size_bytes=%d",
            tool_call_id, image_count, image_bytes,
        )
    return output
