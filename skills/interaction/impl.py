from core.interaction import ask_user as _bridge_ask_user
import json
import mimetypes
import os
import requests
import uuid


def ask_user_confirmation(message):
    result = _bridge_ask_user(message)
    if result is True:
        return "User confirmed (Yes)."
    if result is False:
        return "User denied (No)."
    return f"User replied: {result}"

def ask_user(message):
    return ask_user_confirmation(message)


def _cfg(_context, key, default=""):
    if not isinstance(_context, dict):
        return default
    cfg = _context.get("config_manager")
    if not cfg:
        return default
    try:
        value = cfg.get(key, default)
        return value if value is not None else default
    except Exception:
        return default


def _truncate_text(value, limit=800):
    if value is None:
        return value
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"...<truncated {len(value)}>"
    return value


def _safe_json(value):
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _get_tenant_token(app_id, app_secret):
    if not app_id or not app_secret:
        return None, "missing_app_credentials"
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=8
        )
        body = None
        try:
            body = resp.json()
        except Exception:
            body = _truncate_text(getattr(resp, "text", ""))
        if resp.ok and isinstance(body, dict) and body.get("tenant_access_token"):
            return body.get("tenant_access_token"), ""
        return None, _safe_json(body)
    except Exception as e:
        return None, str(e)


def _validate_receive(receive_id_type, receive_id):
    rid_type = (receive_id_type or "").strip()
    rid = (receive_id or "").strip()
    allowed_types = {"open_id", "chat_id", "user_id", "union_id", "email"}
    if rid_type not in allowed_types:
        return "", "", "invalid_receive_id_type"
    if not rid:
        return "", "", "missing_receive_id"
    return rid_type, rid, ""

def _resolve_receive_target(_context):
    receive_id_type_value = (_cfg(_context, "feishu_receive_id_type", "") or "").strip()
    receive_id_value = (_cfg(_context, "feishu_receive_id", "") or "").strip()
    if receive_id_type_value and receive_id_value:
        return receive_id_type_value, receive_id_value, ""
    event_like = None
    if isinstance(_context, dict):
        event_like = _context.get("im_event") or _context.get("feishu_event")
    if isinstance(event_like, dict):
        chat_id = (event_like.get("chat_id") or "").strip()
        user_id = (event_like.get("user_id") or "").strip()
        if chat_id:
            return "chat_id", chat_id, ""
        if user_id:
            return "open_id", user_id, ""
    if not receive_id_type_value or not receive_id_value:
        return "", "", "missing_receive_target"
    return receive_id_type_value, receive_id_value, ""

def _resolve_local_path(path, _context):
    raw = (path or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        return raw
    candidates = []
    workspace_dir = ""
    if isinstance(_context, dict):
        workspace_dir = (_context.get("workspace_dir") or "").strip()
        if not workspace_dir:
            cfg = _context.get("config_manager")
            if cfg:
                try:
                    workspace_dir = (cfg.get("default_workspace", "") or "").strip()
                except Exception:
                    workspace_dir = ""
    if workspace_dir:
        candidates.append(os.path.abspath(os.path.join(workspace_dir, raw)))
    candidates.append(os.path.abspath(raw))
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0] if candidates else raw


def _upload_image(tenant_token, file_path):
    if not tenant_token or not file_path or not os.path.exists(file_path):
        return None, "missing_token_or_file"
    if os.path.getsize(file_path) <= 0:
        return None, "empty_image_file"
    if os.path.getsize(file_path) > 10 * 1024 * 1024:
        return None, "image_too_large_over_10mb"
    file_name = os.path.basename(file_path)
    mime = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    try:
        with open(file_path, "rb") as f:
            files = {"image": (file_name, f, mime)}
            data = {"image_type": "message"}
            resp = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/images",
                headers={"Authorization": f"Bearer {tenant_token}"},
                data=data,
                files=files,
                timeout=20
            )
        body = None
        try:
            body = resp.json()
        except Exception:
            body = _truncate_text(getattr(resp, "text", ""))
        if resp.ok and isinstance(body, dict) and body.get("code") in (0, "0", None):
            data = body.get("data") or {}
            image_key = data.get("image_key")
            if image_key:
                return image_key, ""
        return None, _safe_json(body)
    except Exception as e:
        return None, str(e)


def _upload_file(tenant_token, file_path):
    if not tenant_token or not file_path or not os.path.exists(file_path):
        return None, "missing_token_or_file"
    if os.path.getsize(file_path) <= 0:
        return None, "empty_file"
    if os.path.getsize(file_path) > 30 * 1024 * 1024:
        return None, "file_too_large_over_30mb"
    file_name = os.path.basename(file_path)
    file_type = _guess_file_type(file_name)
    mime = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    try:
        with open(file_path, "rb") as f:
            files = {"file": (file_name, f, mime)}
            data = {"file_type": file_type, "file_name": file_name}
            resp = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/files",
                headers={"Authorization": f"Bearer {tenant_token}"},
                data=data,
                files=files,
                timeout=30
            )
        body = None
        try:
            body = resp.json()
        except Exception:
            body = _truncate_text(getattr(resp, "text", ""))
        if resp.ok and isinstance(body, dict) and body.get("code") in (0, "0", None):
            data = body.get("data") or {}
            file_key = data.get("file_key")
            if file_key:
                return file_key, ""
        return None, _safe_json(body)
    except Exception as e:
        return None, str(e)


def _as_post_link(title, name, link, caption=""):
    text = f"{name or '文件'}"
    if caption:
        text = f"{text} - {caption}"
    return {
        "zh_cn": {
            "title": title or "AI 助手交付物",
            "content": [
                [
                    {"tag": "text", "text": f"{text}: "},
                    {"tag": "a", "text": "打开", "href": link}
                ]
            ]
        }
    }


def _is_image_item(subtype, mime, name):
    s = (subtype or "").strip().lower()
    if s == "image":
        return True
    m = (mime or "").strip().lower()
    if m.startswith("image/"):
        return True
    ext = os.path.splitext(name or "")[1].lower()
    return ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}

def _guess_file_type(file_name):
    ext = os.path.splitext(file_name or "")[1].lower()
    mapping = {
        ".mp4": "mp4",
        ".pdf": "pdf",
        ".doc": "doc",
        ".docx": "doc",
        ".xls": "xls",
        ".xlsx": "xls",
        ".opus": "opus",
    }
    return mapping.get(ext, "stream")

def _send_feishu_message(tenant_token, receive_id_type, receive_id, msg_type, content):
    if not tenant_token:
        return False, "missing_tenant_token"
    if not receive_id_type or not receive_id:
        return False, "missing_receive_target"
    payload = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": json.dumps(content, ensure_ascii=False),
        "uuid": uuid.uuid4().hex
    }
    try:
        resp = requests.post(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}",
            headers={"Authorization": f"Bearer {tenant_token}"},
            json=payload,
            timeout=12
        )
        body = None
        try:
            body = resp.json()
        except Exception:
            body = _truncate_text(getattr(resp, "text", ""))
        ok = bool(resp.ok and isinstance(body, dict) and body.get("code") in (0, "0", None))
        if ok:
            return True, ""
        return False, _safe_json(body)
    except Exception as e:
        return False, str(e)


def publish_feishu_artifact(
    items,
    audience="feishu",
    tool_summary="",
    card_title="AI 助手交付物",
    _context=None
):
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            return "Error: items must be a list or valid JSON list."
    if not isinstance(items, list) or not items:
        return "Error: items must be a non-empty list."
    audience_value = (audience or "feishu").strip().lower()
    if audience_value != "feishu":
        return "Error: audience must be feishu."
    app_id = (_cfg(_context, "feishu_app_id", "") or "").strip()
    app_secret = (_cfg(_context, "feishu_app_secret", "") or "").strip()
    receive_id_type_value, receive_id_value, receive_error = _resolve_receive_target(_context)
    if not receive_error:
        receive_id_type_value, receive_id_value, receive_error = _validate_receive(receive_id_type_value, receive_id_value)
    tenant_token = None
    token_reason = ""
    if app_id and app_secret and not receive_error:
        tenant_token, token_reason = _get_tenant_token(app_id, app_secret)
    delivery_enabled = bool(tenant_token and not receive_error)
    content_parts = []
    normalized_items = []
    failed = []
    success = []
    skipped = []
    for idx, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            return f"Error: items[{idx}] must be an object."
        path_input = (raw_item.get("path") or "").strip()
        path = _resolve_local_path(path_input, _context) if path_input else ""
        url = (raw_item.get("url") or "").strip()
        name = (raw_item.get("name") or "").strip()
        mime = (raw_item.get("mime") or "").strip()
        subtype = (raw_item.get("subtype") or "").strip().lower()
        caption = (raw_item.get("caption") or "").strip()
        size = raw_item.get("size")
        if path_input and not os.path.exists(path):
            return f"Error: file not found: {path_input}. Please ensure the file exists before publish_feishu_artifact."
        if not path and not url:
            return f"Error: items[{idx}] requires path or url."
        if not name:
            if path:
                name = os.path.basename(path)
            elif url:
                name = os.path.basename(url.split("?", 1)[0].rstrip("/")) or "file"
        if not mime:
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if not subtype and mime.startswith("image/"):
            subtype = "image"
        if size is None and path and os.path.exists(path):
            try:
                size = os.path.getsize(path)
            except Exception:
                size = None
        normalized = {
            "type": "file",
            "subtype": subtype,
            "name": name,
            "path": path,
            "url": url,
            "mime": mime,
            "size": size,
            "caption": caption,
            "audience": audience_value,
            "artifact_source": "publish_feishu_artifact",
        }
        normalized_items.append(normalized)
        is_image = _is_image_item(subtype, mime, name)
        delivered = False
        reason = ""
        if delivery_enabled and path and os.path.exists(path):
            if is_image:
                image_key, upload_reason = _upload_image(tenant_token, path)
                if image_key:
                    ok, send_reason = _send_feishu_message(
                        tenant_token,
                        receive_id_type_value,
                        receive_id_value,
                        "image",
                        {"image_key": image_key}
                    )
                    if ok:
                        delivered = True
                        success.append({"name": name, "type": "image"})
                    else:
                        reason = f"send_image_failed:{send_reason}"
                else:
                    reason = f"upload_image_failed:{upload_reason}"
            else:
                file_key, upload_reason = _upload_file(tenant_token, path)
                if file_key:
                    ok, send_reason = _send_feishu_message(
                        tenant_token,
                        receive_id_type_value,
                        receive_id_value,
                        "file",
                        {"file_key": file_key}
                    )
                    if ok:
                        delivered = True
                        success.append({"name": name, "type": "file"})
                    else:
                        reason = f"send_file_failed:{send_reason}"
                else:
                    reason = f"upload_file_failed:{upload_reason}"
        if delivery_enabled and (not delivered) and url:
            ok, send_reason = _send_feishu_message(
                tenant_token,
                receive_id_type_value,
                receive_id_value,
                "post",
                _as_post_link(card_title, name, url, caption=caption)
            )
            if ok:
                delivered = True
                success.append({"name": name, "type": "post_link"})
            else:
                reason = f"send_post_failed:{send_reason}"
        if not delivered:
            if not reason:
                if not delivery_enabled:
                    reason = "delivery_skipped_missing_runtime_target_or_credentials"
                    skipped.append({"name": name, "reason": reason})
                else:
                    reason = "delivery_failed"
                    failed.append({"name": name, "reason": reason})
            elif delivery_enabled:
                failed.append({"name": name, "reason": reason})
        normalized["delivered"] = delivered
        normalized["delivery_reason"] = reason
    summary = (tool_summary or "").strip() or f"Prepared {len(normalized_items)} artifact(s) for delivery."
    content_parts.append(
        {
            "type": "tool_event",
            "tool_name": "publish_feishu_artifact",
            "status": "completed",
            "summary": summary,
            "source_tool": "publish_feishu_artifact",
        }
    )
    content_parts.extend(normalized_items)
    ok_flag = len(failed) == 0
    delivery_result = {
        "feishu": {
            "ok": ok_flag,
            "reason": f"success={len(success)} failed={len(failed)} skipped={len(skipped)} receive_error={receive_error or 'none'} token_error={token_reason or 'none'}",
            "success": success,
            "failed": failed,
            "skipped": skipped
        }
    }
    payload = {
        "source_tool": "publish_feishu_artifact",
        "content": f"已处理 {len(normalized_items)} 个文件输出，成功 {len(success)}，失败 {len(failed)}，跳过 {len(skipped)}。",
        "content_parts": content_parts,
        "delivery_result": delivery_result,
    }
    return json.dumps(payload, ensure_ascii=False)
