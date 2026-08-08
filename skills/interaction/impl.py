import json
import mimetypes
import os
import requests
import uuid

from core.im_gateway_registry import (
    ARTIFACT_DELIVERY_LINK,
    ARTIFACT_DELIVERY_NATIVE,
    ARTIFACT_DELIVERY_NONE,
    artifact_capable_provider_ids,
    get_provider_spec,
    provider_artifact_delivery_mode,
)
from core.interaction import interaction_service


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


def _run_context(_context):
    if not isinstance(_context, dict):
        return {}
    run_context = _context.get("run_context")
    return run_context if isinstance(run_context, dict) else {}


def _runtime_im_provider(_context):
    ctx = _run_context(_context)
    provider = (ctx.get("im_provider") or ctx.get("channel") or "").strip().lower()
    if get_provider_spec(provider):
        return provider
    if not isinstance(_context, dict):
        return ""
    event_like = _context.get("im_event")
    if isinstance(event_like, dict):
        provider = (event_like.get("provider") or "").strip().lower()
        if get_provider_spec(provider):
            return provider
    if isinstance(_context.get("feishu_event"), dict):
        return "feishu"
    return ""


def _gateway_provider_config(_context, provider_name):
    gateway = _cfg(_context, "im_gateway", {})
    if not isinstance(gateway, dict):
        return {}
    providers = gateway.get("providers")
    if not isinstance(providers, dict):
        return {}
    cfg = providers.get(provider_name)
    return cfg if isinstance(cfg, dict) else {}


def _session_id(_context):
    if not isinstance(_context, dict):
        return ""
    return (
        _context.get("session_id")
        or _context.get("conversation_id")
        or _context.get("workspace_session_id")
        or ""
    )


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


def _result_preview(result):
    if not isinstance(result, dict):
        return str(result)
    content = (result.get("content") or "").strip()
    if content:
        return content
    selected = result.get("selected_options") or []
    if selected:
        return f"selected={', '.join(str(item) for item in selected)}"
    text = (result.get("text") or "").strip()
    if text:
        return text
    return _safe_json(result)


def request_user_approval(
    message,
    title="",
    severity="medium",
    timeout_seconds=120,
    details="",
    _context=None,
):
    response = interaction_service.create_request(
        _session_id(_context),
        "approval",
        message,
        title=title or "请确认",
        allow_free_text=False,
        timeout_seconds=timeout_seconds,
        source_tool="request_user_approval",
        metadata={
            "severity": (severity or "medium").strip().lower(),
            "details": (details or "").strip(),
        },
    )
    return {
        "source_tool": "request_user_approval",
        "content": "用户已批准操作。" if response.get("approved") else f"用户未批准操作（{response.get('status') or 'unknown'}）。",
        "content_parts": [
            {
                "type": "tool_event",
                "tool_name": "request_user_approval",
                "status": response.get("status") or "completed",
                "summary": _result_preview(response),
            }
        ],
        "interaction_request": {
            "kind": "approval",
            "message": message,
            "title": title or "请确认",
            "severity": (severity or "medium").strip().lower(),
            "details": (details or "").strip(),
            "timeout_seconds": timeout_seconds,
        },
        "interaction_response": response,
    }


def request_user_input(
    message,
    title="",
    input_mode="text",
    options=None,
    questions=None,
    purpose="",
    allow_free_text=True,
    timeout_seconds=120,
    _context=None,
):
    custom_option = {"label": "自定义", "value": "__custom__", "description": "选择后填写选项以外的自定义内容。"}
    normalized_purpose = str(purpose or "").strip().lower()
    grilling = str(_run_context(_context).get("mode") or "").strip().lower() == "grilling"

    def _normalize_question_specs(raw_questions):
        normalized = []
        seen_ids = set()
        for item in raw_questions or []:
            if not isinstance(item, dict):
                continue
            qid = str(item.get("id") or "").strip()
            question = str(item.get("question") or "").strip()
            if not qid or not question or qid in seen_ids:
                continue
            seen_ids.add(qid)
            q_options = []
            for option in item.get("options") or []:
                if isinstance(option, dict):
                    label = str(option.get("label") or option.get("value") or "").strip()
                    value = str(option.get("value") or label).strip()
                    if not label or not value:
                        continue
                    q_options.append(
                        {
                            "label": label,
                            "value": value,
                            "description": str(option.get("description") or "").strip(),
                        }
                    )
                elif isinstance(option, str) and option.strip():
                    value = option.strip()
                    q_options.append({"label": value, "value": value, "description": ""})
            if not q_options:
                continue
            q_options = [
                option
                for option in q_options
                if str(option.get("value") or "").strip() != custom_option["value"]
                and str(option.get("label") or "").strip() != custom_option["label"]
            ]
            if not q_options:
                continue
            q_options.append(dict(custom_option))
            normalized.append(
                {
                    "header": str(item.get("header") or "").strip(),
                    "id": qid,
                    "question": question,
                    "options": q_options,
                }
            )
        return normalized

    normalized_questions = _normalize_question_specs(questions)
    if normalized_questions:
        response = interaction_service.create_request(
            _session_id(_context),
            "questionnaire",
            message,
            title=title or "需要你的输入",
            questions=normalized_questions,
            allow_free_text=True,
            timeout_seconds=timeout_seconds,
            source_tool="request_user_input",
            metadata={
                "input_mode": "questionnaire",
                "purpose": normalized_purpose,
                "auto_select_first_on_timeout": not grilling,
            },
        )
        answers = response.get("answers") if isinstance(response.get("answers"), dict) else {}
        answered_count = len(answers)
        response_summary = f"answers={answered_count}"
        return {
            "source_tool": "request_user_input",
            "purpose": normalized_purpose,
            "content": (
                f"已收到用户输入，共回答 {answered_count} 个问题。"
                if response.get("approved")
                else f"未收到有效输入（{response.get('status') or 'unknown'}）。"
            ),
            "content_parts": [
                {
                    "type": "tool_event",
                    "tool_name": "request_user_input",
                    "status": response.get("status") or "completed",
                    "summary": response_summary,
                }
            ],
            "interaction_request": {
                "kind": "questionnaire",
                "message": message,
                "title": title or "需要你的输入",
                "questions": normalized_questions,
                "purpose": normalized_purpose,
                "allow_free_text": True,
                "timeout_seconds": timeout_seconds,
            },
            "interaction_response": response,
            "answers": answers,
        }

    mode = (input_mode or "text").strip().lower()
    if mode not in {"text", "choice", "multi_choice"}:
        mode = "text"
    normalized_options = []
    for item in options or []:
        if isinstance(item, dict):
            normalized_options.append(
                {
                    "label": str(item.get("label") or item.get("value") or "").strip(),
                    "value": str(item.get("value") or item.get("label") or "").strip(),
                    "description": str(item.get("description") or "").strip(),
                }
            )
        elif isinstance(item, str) and item.strip():
            normalized_options.append({"label": item.strip(), "value": item.strip(), "description": ""})
    response = interaction_service.create_request(
        _session_id(_context),
        mode,
        message,
        title=title or "需要你的输入",
        options=normalized_options,
        allow_free_text=bool(allow_free_text),
        timeout_seconds=timeout_seconds,
        source_tool="request_user_input",
        metadata={"input_mode": mode, "purpose": normalized_purpose},
    )
    selection_preview = response.get("selected_options") or []
    response_summary = _result_preview(response)
    if selection_preview:
        response_summary = f"{response_summary} | options={', '.join(str(item) for item in selection_preview)}"
    return {
        "source_tool": "request_user_input",
        "purpose": normalized_purpose,
        "content": f"已收到用户输入：{response_summary}" if response.get("approved") else f"未收到有效输入（{response.get('status') or 'unknown'}）。",
        "content_parts": [
            {
                "type": "tool_event",
                "tool_name": "request_user_input",
                "status": response.get("status") or "completed",
                "summary": response_summary,
            }
        ],
        "interaction_request": {
            "kind": mode,
            "message": message,
            "title": title or "需要你的输入",
            "options": normalized_options,
            "questions": [],
            "purpose": normalized_purpose,
            "allow_free_text": bool(allow_free_text),
            "timeout_seconds": timeout_seconds,
        },
        "interaction_response": response,
        "answers": response.get("answers") if isinstance(response.get("answers"), dict) else {},
    }


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


def _resolve_receive_target(_context, provider_cfg=None):
    provider_cfg = provider_cfg if isinstance(provider_cfg, dict) else {}
    receive_id_type_value = (provider_cfg.get("receive_id_type") or "").strip()
    receive_id_value = (provider_cfg.get("receive_id") or "").strip()
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
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
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


def _send_webhook_markdown(webhook_url, provider_name, title, text):
    if not webhook_url:
        return False, "missing_webhook_url"
    provider_name = (provider_name or "").strip().lower()
    if provider_name == "wecom":
        payload = {"msgtype": "markdown", "markdown": {"content": text or ""}}
    else:
        payload = {"msgtype": "markdown", "markdown": {"title": title or "AI 助手交付物", "text": text or ""}}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=12)
        if resp.ok:
            return True, ""
        body = None
        try:
            body = resp.json()
        except Exception:
            body = _truncate_text(getattr(resp, "text", ""))
        return False, _safe_json(body)
    except Exception as e:
        return False, str(e)


def publish_artifacts(
    items,
    audience="auto",
    summary="",
    title="AI 助手交付物",
    _context=None,
):
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            return {"error": "items must be a list or valid JSON list."}
    if not isinstance(items, list) or not items:
        return {"error": "items must be a non-empty list."}

    audience_value = (audience or "auto").strip().lower()
    supported_providers = set(artifact_capable_provider_ids())
    if audience_value not in {"auto", *supported_providers}:
        choices = ", ".join(["auto", *artifact_capable_provider_ids()])
        return {"error": f"audience must be one of: {choices}."}
    runtime_provider = _runtime_im_provider(_context)
    runtime_delivery_mode = provider_artifact_delivery_mode(runtime_provider)
    if runtime_delivery_mode == ARTIFACT_DELIVERY_NONE:
        return {"error": "publish_artifacts is not available for the current messaging channel."}
    target_provider = runtime_provider if audience_value == "auto" else audience_value
    target_delivery_mode = provider_artifact_delivery_mode(target_provider)
    provider_cfg = _gateway_provider_config(_context, target_provider)

    tenant_token = None
    token_reason = ""
    receive_error = ""
    receive_id_type_value = ""
    receive_id_value = ""
    target_enabled = False
    if target_delivery_mode == ARTIFACT_DELIVERY_NATIVE:
        app_id = (provider_cfg.get("app_id") or "").strip()
        app_secret = (provider_cfg.get("app_secret") or "").strip()
        receive_id_type_value, receive_id_value, receive_error = _resolve_receive_target(_context, provider_cfg)
        if not receive_error:
            receive_id_type_value, receive_id_value, receive_error = _validate_receive(receive_id_type_value, receive_id_value)
        if app_id and app_secret and not receive_error:
            tenant_token, token_reason = _get_tenant_token(app_id, app_secret)
        target_enabled = bool(tenant_token and not receive_error)
    else:
        target_enabled = bool((provider_cfg.get("webhook_url") or "").strip())

    content_parts = []
    normalized_items = []
    failed = []
    success = []
    skipped = []

    for idx, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            return {"error": f"items[{idx}] must be an object."}
        path_input = (raw_item.get("path") or "").strip()
        path = _resolve_local_path(path_input, _context) if path_input else ""
        url = (raw_item.get("url") or "").strip()
        name = (raw_item.get("name") or "").strip()
        mime = (raw_item.get("mime") or "").strip()
        subtype = (raw_item.get("subtype") or "").strip().lower()
        caption = (raw_item.get("caption") or "").strip()
        size = raw_item.get("size")

        if path_input and not os.path.exists(path):
            return {"error": f"file not found: {path_input}. Please ensure the file exists before publish_artifacts."}
        if not path and not url:
            return {"error": f"items[{idx}] requires path or url."}
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
            "artifact_source": "publish_artifacts",
        }
        normalized_items.append(normalized)

        is_image = _is_image_item(subtype, mime, name)
        delivered = False
        reason = ""
        if target_delivery_mode == ARTIFACT_DELIVERY_NATIVE and target_enabled and path and os.path.exists(path):
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
        if target_delivery_mode == ARTIFACT_DELIVERY_NATIVE and target_enabled and (not delivered) and url:
            ok, send_reason = _send_feishu_message(
                tenant_token,
                receive_id_type_value,
                receive_id_value,
                "post",
                _as_post_link(title, name, url, caption=caption)
            )
            if ok:
                delivered = True
                success.append({"name": name, "type": "post_link"})
            else:
                reason = f"send_post_failed:{send_reason}"
        if target_delivery_mode == ARTIFACT_DELIVERY_LINK and target_enabled and url:
            webhook_url = (provider_cfg.get("webhook_url") or "").strip()
            text = f"**{title or 'AI 助手交付物'}**\n\n- {name}: {url}"
            if caption:
                text += f"\n  {caption}"
            ok, send_reason = _send_webhook_markdown(webhook_url, target_provider, title, text)
            if ok:
                delivered = True
                success.append({"name": name, "type": "link"})
            else:
                reason = f"send_markdown_failed:{send_reason}"
        if not delivered:
            if not reason:
                if target_delivery_mode == ARTIFACT_DELIVERY_NATIVE and not target_enabled:
                    reason = "delivery_skipped_missing_runtime_target_or_credentials"
                    skipped.append({"name": name, "reason": reason})
                elif target_delivery_mode == ARTIFACT_DELIVERY_LINK and not target_enabled:
                    reason = "delivery_skipped_missing_webhook_url"
                    skipped.append({"name": name, "reason": reason})
                elif target_delivery_mode == ARTIFACT_DELIVERY_LINK and path and not url:
                    reason = "delivery_skipped_native_file_upload_not_available"
                    skipped.append({"name": name, "reason": reason})
                else:
                    reason = "delivery_failed"
                    failed.append({"name": name, "reason": reason})
            elif target_enabled:
                failed.append({"name": name, "reason": reason})
            else:
                skipped.append({"name": name, "reason": reason})
        normalized["delivered"] = delivered
        normalized["delivery_reason"] = reason

    summary_text = (summary or "").strip() or f"Prepared {len(normalized_items)} artifact(s) for delivery."
    content_parts.append(
        {
            "type": "tool_event",
            "tool_name": "publish_artifacts",
            "status": "completed",
            "summary": summary_text,
            "source_tool": "publish_artifacts",
        }
    )
    content_parts.extend(normalized_items)

    delivery_result = {
        target_provider: {
            "ok": len(failed) == 0,
            "enabled": target_enabled,
            "reason": f"success={len(success)} failed={len(failed)} skipped={len(skipped)} receive_error={receive_error or 'none'} token_error={token_reason or 'none'}",
            "success": success,
            "failed": failed,
            "skipped": skipped,
        },
    }
    return {
        "source_tool": "publish_artifacts",
        "content": f"已处理 {len(normalized_items)} 个文件输出，{target_provider} 成功 {len(success)}，失败 {len(failed)}，跳过 {len(skipped)}。",
        "content_parts": content_parts,
        "delivery_result": delivery_result,
    }


TOOL_EXPORTS = [
    {
        "name": "request_user_approval",
        "handler": request_user_approval,
        "description": "Ask the user to approve or reject a potentially important action.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The approval message shown to the user."},
                "title": {"type": "string", "description": "Optional short dialog title."},
                "severity": {"type": "string", "description": "Risk level hint: low, medium, or high."},
                "timeout_seconds": {"type": "number", "description": "How long to wait before timing out."},
                "details": {"type": "string", "description": "Optional extra context shown with the approval request."},
            },
            "required": ["message"],
        },
        "kind": "interaction_request",
        "requires_user_interaction": True,
        "result_format": "structured_json",
    },
    {
        "name": "request_user_input",
        "handler": request_user_input,
        "description": "Ask the user for choices or a multi-question questionnaire. For clarification, provide mutually exclusive choices with the recommended option first; the system appends the custom option.",
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The prompt shown to the user."},
                "title": {"type": "string", "description": "Optional short dialog title."},
                "input_mode": {"type": "string", "description": "One of text, choice, or multi_choice."},
                "purpose": {
                    "type": "string",
                    "description": "Optional runtime purpose. Use grill_checkpoint only for the grilling summary decision.",
                },
                "options": {
                    "type": "array",
                    "description": "Optional list of choices.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["label"],
                    },
                },
                "questions": {
                    "type": "array",
                    "description": "Optional questionnaire definition. When provided, input_mode/options are ignored. Every question must include choices; the first is treated as recommended and a custom option is appended last.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "header": {"type": "string"},
                            "id": {"type": "string"},
                            "question": {"type": "string"},
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "value": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["label"],
                                },
                            },
                        },
                        "required": ["id", "question", "options"],
                    },
                },
                "allow_free_text": {"type": "boolean", "description": "Whether arbitrary text is allowed besides listed options."},
                "timeout_seconds": {"type": "number", "description": "How long to wait before timing out."},
            },
            "required": ["message"],
        },
        "kind": "interaction_request",
        "requires_user_interaction": True,
        "result_format": "structured_json",
    },
    {
        "name": "publish_artifacts",
        "handler": publish_artifacts,
        "description": "Publish generated files, images, or links to transcript and optional IM delivery channels.",
        "parameters": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "description": "Artifacts to publish.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "url": {"type": "string"},
                            "name": {"type": "string"},
                            "mime": {"type": "string"},
                            "subtype": {"type": "string"},
                            "caption": {"type": "string"},
                        },
                    },
                },
                "audience": {"type": "string", "description": "One of auto, feishu, dingtalk, or wecom for an artifact-capable messaging channel."},
                "summary": {"type": "string", "description": "Summary text for timeline display."},
                "title": {"type": "string", "description": "Title used for IM post link messages."},
            },
            "required": ["items"],
        },
        "kind": "artifact_delivery",
        "result_format": "structured_json",
    },
]
