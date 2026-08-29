"""Generated-image conversation parts and durable session-local storage."""

from __future__ import annotations

import base64
import binascii
from io import BytesIO
import os
import re
import tempfile

from PIL import Image


MAX_GENERATED_IMAGE_BYTES = 25 * 1024 * 1024
_SUPPORTED_FORMATS = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
}


class GeneratedImageError(RuntimeError):
    """A generated image could not be validated or stored safely."""


def session_attachment_dir(history_dir, session_id):
    safe_session_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(session_id or "").strip())
    if not safe_session_id:
        raise GeneratedImageError("图片生成结果缺少有效的会话标识，无法安全保存。")
    return os.path.join(os.path.abspath(history_dir), "attachments", safe_session_id)


def output_image_parts(content_parts):
    if not isinstance(content_parts, list):
        return []
    return [
        dict(part)
        for part in content_parts
        if isinstance(part, dict)
        and str(part.get("type") or "").strip().lower() == "output_image"
    ]


def has_visible_assistant_output(content, content_parts=None):
    return bool(str(content or "").strip() or output_image_parts(content_parts))


def persist_generated_image(history_dir, session_id, source_item_id, encoded_image):
    item_id = str(source_item_id or "").strip()
    if not item_id:
        raise GeneratedImageError("图片生成结果缺少 item id，无法建立可恢复的会话记录。")
    if not isinstance(encoded_image, str) or not encoded_image.strip():
        raise GeneratedImageError(f"图片生成结果 {item_id} 缺少 base64 数据。")
    encoded_image = encoded_image.strip()
    max_encoded_length = 4 * ((MAX_GENERATED_IMAGE_BYTES + 2) // 3)
    if len(encoded_image) > max_encoded_length:
        raise GeneratedImageError(
            f"图片生成结果 {item_id} 超过 {MAX_GENERATED_IMAGE_BYTES // (1024 * 1024)} MiB 上限。"
        )
    try:
        raw = base64.b64decode(encoded_image, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise GeneratedImageError(f"图片生成结果 {item_id} 不是有效的 base64 数据。") from exc
    if not raw:
        raise GeneratedImageError(f"图片生成结果 {item_id} 为空。")
    if len(raw) > MAX_GENERATED_IMAGE_BYTES:
        raise GeneratedImageError(
            f"图片生成结果 {item_id} 超过 {MAX_GENERATED_IMAGE_BYTES // (1024 * 1024)} MiB 上限。"
        )
    try:
        with Image.open(BytesIO(raw)) as image:
            image_format = str(image.format or "").upper()
            image.verify()
    except Exception as exc:
        raise GeneratedImageError(f"图片生成结果 {item_id} 无法解码。") from exc
    if image_format not in _SUPPORTED_FORMATS:
        raise GeneratedImageError(
            f"图片生成结果 {item_id} 使用了不支持的格式 {image_format or 'unknown'}。"
        )

    mime_type, extension = _SUPPORTED_FORMATS[image_format]
    safe_item_id = re.sub(r"[^A-Za-z0-9._-]+", "_", item_id).strip("._-") or "image"
    target_dir = session_attachment_dir(history_dir, session_id)
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, f"generated-{safe_item_id}{extension}")
    temporary_path = ""
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{safe_item_id}-",
            suffix=".tmp",
            dir=target_dir,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target_path)
        temporary_path = ""
    except OSError as exc:
        raise GeneratedImageError(f"图片生成结果 {item_id} 保存失败：{exc}") from exc
    finally:
        if temporary_path and os.path.exists(temporary_path):
            try:
                os.remove(temporary_path)
            except OSError:
                pass

    return {
        "type": "output_image",
        "path": target_path,
        "name": os.path.basename(target_path),
        "mime_type": mime_type,
        "source_item_id": item_id,
    }
