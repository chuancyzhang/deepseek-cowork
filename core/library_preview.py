"""Read-only library previews; binary files never enter durable job JSON."""
import hashlib
import logging
import os
from pathlib import Path
import uuid

from core.desktop_jobs import JobCancelled
from core.deliverable_editing import decode_text_bytes
from core.file_capabilities import file_capability
from core.knowledge_library import response_data, segment

log = logging.getLogger(__name__)


def check_cancelled(cancelled):
    if cancelled.is_set():
        raise JobCancelled("已取消读取资料。")


def weknora_preview(provider, scope, item, data_dir, cancelled, progress):
    check_cancelled(cancelled)
    progress("正在读取文件信息…")
    info = response_data(provider.request(scope, "GET", f"/api/v1/knowledge/{segment(item)}", cancelled=cancelled.is_set))
    if info.get("type") == "manual" and isinstance(info.get("metadata"), dict):
        text = info["metadata"].get("content")
        if isinstance(text, str):
            return {"text": text, "format": "markdown"}
    name = str(info.get("file_name") or info.get("title") or "资料")
    extension = "." + str(info.get("file_type") or "").lower().lstrip(".")
    if not file_capability("preview" + extension):
        extension = Path(name).suffix.lower()
    capability = file_capability("preview" + extension)
    if not capability:
        raise ValueError("此文件类型暂不支持内置预览，请在原平台打开。")
    progress("正在下载预览文件…")
    raw = provider.preview_html(scope, item, cancelled.is_set, preview_limit=25 * 1024 * 1024)
    check_cancelled(cancelled)
    kind = capability.preview_kind
    if kind in {"markdown", "text", "html"}:
        text, *_ = decode_text_bytes(raw)
        log.info("library_preview_ready source=weknora kind=%s bytes=%s", kind, len(raw))
        return {"html": text} if kind == "html" else {"text": text, "format": kind}
    # Content-addressed copies are private to the connected identity. Job results
    # contain only paths; FileHost grants a URL after a fresh identity check.
    identity = hashlib.sha256(str((scope.get("connection_id"), scope.get("user_id"), scope.get("tenant_id"))).encode()).hexdigest()
    digest = hashlib.sha256(raw).hexdigest()
    root = Path(data_dir) / "library-previews" / identity
    root.mkdir(parents=True, exist_ok=True)
    path = root / (digest + extension)
    if not path.exists():
        temporary = root / (uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_bytes(raw)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    payload = {"kind": "preview", "preview_kind": kind, "read_only": True}
    if extension in {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}:
        from core.document_preview import render_office_pdf
        progress("正在生成文档页面…")
        try:
            preview, _ = render_office_pdf(path, cancel=cancelled, renderer="libreoffice")
        except Exception:
            check_cancelled(cancelled)
            raise
        payload.update(preview_kind="pdf", preview_path=preview)
    elif kind not in {"image", "pdf"}:
        raise ValueError("此文件类型暂不支持内置预览，请在原平台打开。")
    check_cancelled(cancelled)
    log.info("library_preview_ready source=weknora kind=%s bytes=%s", payload["preview_kind"], len(raw))
    return {"kind": "library_preview", "scope": scope, "path": str(path), "name": name,
            "fingerprint": digest, "payload": payload}


def document_content(value):
    """Unwrap known document/content block envelopes without showing API JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(filter(None, (document_content(item) for item in value)))
    if isinstance(value, dict):
        for key in ("markdown", "text", "content", "parsed_content", "parse_content", "body", "blocks", "data"):
            if key in value:
                content = document_content(value[key])
                if content:
                    return content
    return ""
