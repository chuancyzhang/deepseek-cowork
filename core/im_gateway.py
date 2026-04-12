import base64
import hashlib
import hmac
import json
import requests
import os
import uuid
import time
import threading
import sys
from core.chat_storage import ChatStorage
from core.config_manager import ConfigManager
from core.daemon import DaemonClient, DEFAULT_HOST, DEFAULT_PORT
from core.env_utils import ensure_package_installed, get_app_data_dir, get_python_executable
from core.interaction import parse_interaction_reply
from core.im_session_key import build_im_session_key, resolve_date_key

_RECENT_MESSAGE_IDS = {}
_RECENT_LOCK = threading.Lock()
_MESSAGE_ID_TTL = 30
_RECENT_ACTION_IDS = {}
_RECENT_ACTION_LOCK = threading.Lock()
_ACTION_ID_TTL = 300
_CARD_CONTEXT = {}
_CARD_CONTEXT_LOCK = threading.Lock()

def _log_gateway(message):
    try:
        log_dir = get_app_data_dir()
        log_path = os.path.join(log_dir, "im_gateway.log")
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception:
        try:
            print(f"[im_gateway] {message}", file=sys.stderr)
        except Exception:
            return

def _truncate_text(value, limit=800):
    if value is None:
        return value
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"...<truncated {len(value)}>"
    return value

def _sanitize(value):
    sensitive_keys = {
        "app_secret",
        "tenant_access_token",
        "Authorization",
        "authorization",
        "encrypt_key",
        "encrypt",
        "verification_token"
    }
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if k in sensitive_keys:
                if k == "encrypt":
                    raw = v or ""
                    try:
                        raw_len = len(raw)
                    except Exception:
                        raw_len = 0
                    result[k] = f"<redacted len={raw_len}>"
                else:
                    result[k] = "<redacted>"
            else:
                result[k] = _sanitize(v)
        return result
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, str):
        return _truncate_text(value)
    return value

def _safe_json_dump(value):
    try:
        return json.dumps(_sanitize(value), ensure_ascii=False)
    except Exception:
        try:
            return str(_sanitize(value))
        except Exception:
            return "<unserializable>"


def _is_context_overflow_error(error_text):
    text = (error_text or "").lower()
    if not text:
        return False
    markers = [
        "context length",
        "maximum context",
        "too many tokens",
        "context_window_exceeded",
        "maximum context length",
    ]
    return any(marker in text for marker in markers)

def _strip_think_blocks(text, state):
    if not text:
        return "", state
    combined = (state.get("buffer") or "") + text
    state["buffer"] = ""
    output = ""
    i = 0
    in_think = bool(state.get("in_think"))
    while i < len(combined):
        if in_think:
            end = combined.find("</think>", i)
            if end == -1:
                state["in_think"] = True
                state["buffer"] = combined[i:]
                return output, state
            i = end + len("</think>")
            in_think = False
            state["in_think"] = False
            continue
        start = combined.find("<think>", i)
        if start == -1:
            output += combined[i:]
            break
        output += combined[i:start]
        i = start + len("<think>")
        in_think = True
    state["in_think"] = in_think
    return output, state

def _seen_message(message_id):
    if not message_id:
        return False
    now = time.time()
    with _RECENT_LOCK:
        expired = [mid for mid, ts in _RECENT_MESSAGE_IDS.items() if now - ts > _MESSAGE_ID_TTL]
        for mid in expired:
            _RECENT_MESSAGE_IDS.pop(mid, None)
        if message_id in _RECENT_MESSAGE_IDS:
            return True
        _RECENT_MESSAGE_IDS[message_id] = now
    return False


def _seen_action(action_key):
    if not action_key:
        return False
    now = time.time()
    with _RECENT_ACTION_LOCK:
        expired = [aid for aid, ts in _RECENT_ACTION_IDS.items() if now - ts > _ACTION_ID_TTL]
        for aid in expired:
            _RECENT_ACTION_IDS.pop(aid, None)
        if action_key in _RECENT_ACTION_IDS:
            return True
        _RECENT_ACTION_IDS[action_key] = now
    return False

def _consume_feedback_prefix(conversation_id, text):
    if not conversation_id:
        return text
    with _CARD_CONTEXT_LOCK:
        for _, ctx in _CARD_CONTEXT.items():
            if not isinstance(ctx, dict):
                continue
            if ctx.get("conversation_id") != conversation_id:
                continue
            if not ctx.get("awaiting_feedback"):
                continue
            ctx["awaiting_feedback"] = False
            feedback_text = (text or "").strip()
            return f"[用户反馈]\n{feedback_text}\n\n请先确认收到反馈，并基于反馈给出修正后的答复。"
    return text

def _extract_publish_artifacts_payload(value):
    obj = None
    if isinstance(value, dict):
        obj = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw.startswith("{"):
            return None
        try:
            obj = json.loads(raw)
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None
    if (obj.get("source_tool") or "").strip() != "publish_artifacts":
        return None
    content_parts = obj.get("content_parts")
    if not isinstance(content_parts, list):
        return None
    safe_parts = [p for p in content_parts if isinstance(p, dict)]
    delivery_result = obj.get("delivery_result")
    if not isinstance(delivery_result, dict):
        delivery_result = {}
    return {"content_parts": safe_parts, "delivery_result": delivery_result}


def _build_interaction_hint(request):
    if not isinstance(request, dict):
        return "需要你的输入。"
    kind = (request.get("kind") or "text").strip().lower()
    title = (request.get("title") or "需要你的输入").strip()
    message = (request.get("message") or "").strip()
    options = request.get("options") if isinstance(request.get("options"), list) else []
    timeout_seconds = request.get("timeout_seconds") or 120
    lines = [title]
    if message:
        lines.append(message)
    if kind == "approval":
        lines.append("")
        lines.append("请回复：是 / 否")
    elif kind == "choice":
        lines.append("")
        lines.append("请回复以下任一编号或选项文本：")
        for idx, option in enumerate(options, start=1):
            label = (option.get("label") or option.get("value") or "").strip()
            description = (option.get("description") or "").strip()
            lines.append(f"{idx}. {label}" + (f" - {description}" if description else ""))
    elif kind == "multi_choice":
        lines.append("")
        lines.append("请回复一个或多个编号/选项文本，可用逗号分隔：")
        for idx, option in enumerate(options, start=1):
            label = (option.get("label") or option.get("value") or "").strip()
            description = (option.get("description") or "").strip()
            lines.append(f"{idx}. {label}" + (f" - {description}" if description else ""))
    else:
        lines.append("")
        lines.append("请直接回复你的文本输入。")
    lines.append(f"超时约 {int(float(timeout_seconds))} 秒后将自动取消。")
    return "\n".join(line for line in lines if line is not None)


def _build_feishu_model_input(event):
    user_text = (event or {}).get("text") or ""
    channel_hint = (
        "[渠道上下文]\n"
        "- 当前交互渠道: 飞书\n"
        "- 你正在处理飞书会话消息，请优先使用适配飞书的交互方式。\n"
        "- 若需要交付文件或图片，请调用 publish_artifacts；需要触达飞书时将 audience 设为 'feishu' 或 'auto'。\n"
    )
    return f"{channel_hint}\n[用户消息]\n{user_text}"


LARK_AVAILABLE = False

def _try_import_lark():
    global LARK_AVAILABLE, lark, larkcore, larkws, EventDispatcherHandler
    try:
        import lark_oapi as lark
        import lark_oapi.core as larkcore
        import lark_oapi.ws as larkws
        from lark_oapi import EventDispatcherHandler
        LARK_AVAILABLE = True
        return True
    except Exception as e:
        LARK_AVAILABLE = False
        _log_gateway(f"lark_oapi import failed: {e}")
        return False

def _extend_lark_sys_path():
    candidates = []
    python_exe = get_python_executable()
    if python_exe and os.path.exists(python_exe):
        candidates.append(os.path.join(os.path.dirname(python_exe), "Lib", "site-packages"))
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates.append(os.path.join(base_dir, ".venv", "Lib", "site-packages"))
    for p in candidates:
        if p and os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)
            _log_gateway(f"lark_oapi sys.path add: {p}")

_try_import_lark()

_log_gateway(f"im_gateway module loaded, data_dir={get_app_data_dir()}")


def _load_lark_sdk():
    if LARK_AVAILABLE:
        return True
    _extend_lark_sys_path()
    if _try_import_lark():
        return True
    try:
        ensure_package_installed("lark-oapi", "lark_oapi")
    except Exception as e:
        _log_gateway(f"lark_oapi install failed: {e}")
        return False
    _extend_lark_sys_path()
    return _try_import_lark()


class IMProvider:
    name = ""

    def verify_signature(self, request, raw_body):
        return True

    def parse_event(self, payload):
        return None

    def build_reply(self, text):
        return {"msgtype": "text", "text": {"content": text}}

    def send_message(self, text, event=None):
        return self.build_reply(text)

class FeishuProvider(IMProvider):
    name = "feishu"

    def __init__(self, config_manager):
        self.app_id = config_manager.get("feishu_app_id", "")
        self.app_secret = config_manager.get("feishu_app_secret", "")
        self.verification_token = config_manager.get("feishu_verification_token", "")
        self.encrypt_key = config_manager.get("feishu_encrypt_key", "")
        self._token_cache = {"token": None, "expire_at": 0}
        self._card_sequences = {}

    def verify_signature(self, request, raw_body):
        if not self.encrypt_key:
            return True
        ts = request.headers.get("X-Lark-Request-Timestamp") or ""
        nonce = request.headers.get("X-Lark-Request-Nonce") or ""
        sig = request.headers.get("X-Lark-Signature") or ""
        if not ts or not nonce or not sig:
            return False
        to_sign = f"{ts}\n{nonce}\n{raw_body.decode('utf-8')}".encode("utf-8")
        calc = base64.b64encode(hmac.new(self.encrypt_key.encode("utf-8"), to_sign, hashlib.sha256).digest()).decode("utf-8")
        return calc == sig

    def _pkcs7_unpad(self, data):
        pad = data[-1]
        if isinstance(pad, str):
            pad = ord(pad)
        return data[:-pad]

    def _decrypt(self, encrypt_b64):
        if not self.encrypt_key:
            return None
        from Crypto.Cipher import AES
        try:
            key = base64.b64decode(self.encrypt_key)
            iv = key[:16]
            cipher = AES.new(key, AES.MODE_CBC, iv)
            raw = cipher.decrypt(base64.b64decode(encrypt_b64))
            raw = self._pkcs7_unpad(raw)
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            _log_gateway(f"feishu decrypt failed: {e}")
            return None

    def parse_event(self, payload):
        _log_gateway(f"feishu parse_event payload={_safe_json_dump(payload)}")
        if isinstance(payload, dict) and payload.get("type") == "url_verification":
            if self.verification_token and payload.get("token") != self.verification_token:
                _log_gateway("feishu url_verification token mismatch")
                return None
            _log_gateway("feishu url_verification challenge returned")
            return {"challenge": payload.get("challenge", "")}
        data = payload
        if isinstance(payload, dict) and payload.get("encrypt"):
            data = self._decrypt(payload.get("encrypt"))
        if not isinstance(data, dict):
            _log_gateway("feishu parse_event invalid data after decrypt")
            return None
        header = data.get("header") or {}
        if self.verification_token and header.get("token") != self.verification_token:
            _log_gateway("feishu event token mismatch")
            return None
        if self.app_id and header.get("app_id") != self.app_id:
            _log_gateway("feishu event app_id mismatch")
            return None
        if "challenge" in data:
            _log_gateway("feishu event challenge returned")
            return {"challenge": data.get("challenge")}
        event_type = header.get("event_type") or ""
        event = data.get("event") or {}
        message = event.get("message") or {}
        content = message.get("content") or ""
        text = ""
        if isinstance(content, str):
            try:
                content_json = json.loads(content)
                text = content_json.get("text") or ""
            except Exception:
                text = content
        sender = event.get("sender") or {}
        sender_id = sender.get("sender_id") or {}
        sender_type = sender.get("sender_type") or sender_id.get("sender_type")
        user_id = sender_id.get("open_id") or sender_id.get("user_id") or sender_id.get("union_id") or "unknown"
        message_id = message.get("message_id")
        chat_id = message.get("chat_id")
        message_type = message.get("message_type")
        event_id = header.get("event_id")
        create_time = message.get("create_time") or header.get("create_time")
        if not text:
            if event_type:
                _log_gateway(f"feishu event without text type={event_type}")
                return {"event_type": event_type, "event": data}
            _log_gateway("feishu event without text and type")
            return None
        parsed = {
            "user_id": user_id,
            "text": text,
            "message_id": message_id,
            "chat_id": chat_id,
            "message_type": message_type,
            "sender_type": sender_type,
            "event_id": event_id,
            "create_time": create_time,
            "event_type": event_type,
            "event": data
        }
        _log_gateway(f"feishu event parsed={_safe_json_dump(parsed)}")
        return parsed

    def _get_tenant_token(self):
        now = time.time()
        cached = self._token_cache.get("token")
        expire_at = self._token_cache.get("expire_at", 0)
        if cached and expire_at - 60 > now:
            return cached
        try:
            token_resp = requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=5
            )
            token_body = None
            try:
                token_body = token_resp.json()
            except Exception:
                token_body = _truncate_text(getattr(token_resp, "text", ""))
            _log_gateway(f"feishu token_resp status={token_resp.status_code} ok={token_resp.ok} body={_safe_json_dump(token_body)}")
            if token_resp.ok:
                token_data = token_body or {}
                tenant_token = token_data.get("tenant_access_token")
                expires_in = token_data.get("expire") or token_data.get("expire_in") or 0
                if tenant_token:
                    self._token_cache["token"] = tenant_token
                    self._token_cache["expire_at"] = now + int(expires_in or 0)
                    return tenant_token
        except Exception as e:
            _log_gateway(f"feishu token request failed: {e}")
        return None

    def _next_card_sequence(self, card_id):
        seq = self._card_sequences.get(card_id, 0) + 1
        self._card_sequences[card_id] = seq
        return seq

    def _build_card_json(self, content, title="🤖 AI 助手", thinking=None, collapse_thinking=False, content_parts=None, interactive_actions=None):
        elements = []
        if thinking:
            thinking_display = "思考过程已折叠" if collapse_thinking else thinking
            elements.append({"tag": "markdown", "content": f"**思考过程**\n{thinking_display}"})
        elements.append({"tag": "markdown", "content": content})
        file_lines = []
        if isinstance(content_parts, list):
            for part in content_parts:
                if not isinstance(part, dict):
                    continue
                part_type = (part.get("type") or "").strip().lower()
                if part_type == "file":
                    file_name = part.get("name") or "file"
                    file_link = part.get("url") or part.get("path") or ""
                    caption = part.get("caption") or ""
                    line = f"- {file_name}"
                    if file_link:
                        line += f": {file_link}"
                    if caption:
                        line += f" ({caption})"
                    file_lines.append(line)
        if file_lines:
            file_block = "**文件输出**\n" + "\n".join(file_lines[-6:])
            elements.append({"tag": "markdown", "content": file_block})
        return {
            "schema": "2.0",
            "config": {"update_multi": True, "streaming_mode": True, "summary": {"content": "已完成" if collapse_thinking else "生成中"}},
            "header": {"title": {"tag": "plain_text", "content": title}},
            "body": {"elements": elements}
        }

    def _build_card_json_v1(self, content, title="🤖 AI 助手", thinking=None, collapse_thinking=False):
        elements = []
        if thinking:
            thinking_display = "思考过程已折叠" if collapse_thinking else thinking
            elements.append({"tag": "markdown", "content": f"**思考过程**\n{thinking_display}"})
        elements.append({"tag": "markdown", "content": content})
        return {
            "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": elements
        }

    def create_card_entity(self, content, title="🤖 AI 助手"):
        tenant_token = self._get_tenant_token()
        if not tenant_token:
            return None
        card_data = self._build_card_json(content, title=title)
        payload = {"type": "card_json", "data": json.dumps(card_data, ensure_ascii=False)}
        try:
            resp = requests.post(
                "https://open.feishu.cn/open-apis/cardkit/v1/cards",
                headers={"Authorization": f"Bearer {tenant_token}"},
                json=payload,
                timeout=5
            )
            body = None
            try:
                body = resp.json()
            except Exception:
                body = _truncate_text(getattr(resp, "text", ""))
            _log_gateway(f"feishu card_create status={resp.status_code} ok={resp.ok} body={_safe_json_dump(body)}")
            if resp.ok and isinstance(body, dict):
                data = body.get("data") or {}
                return data.get("card_id")
        except Exception as e:
            _log_gateway(f"feishu card_create failed: {e}")
        return None

    def update_card_content(self, card_id, content, title="🤖 AI 助手"):
        tenant_token = self._get_tenant_token()
        if not tenant_token:
            return False
        card_data = self._build_card_json(content, title=title)
        payload = {
            "card": {"type": "card_json", "data": json.dumps(card_data, ensure_ascii=False)},
            "sequence": self._next_card_sequence(card_id)
        }
        try:
            resp = requests.patch(
                f"https://open.feishu.cn/open-apis/cardkit/v1/cards/{card_id}",
                headers={"Authorization": f"Bearer {tenant_token}"},
                json=payload,
                timeout=5
            )
            body = None
            try:
                body = resp.json()
            except Exception:
                body = _truncate_text(getattr(resp, "text", ""))
            _log_gateway(f"feishu card_update status={resp.status_code} ok={resp.ok} body={_safe_json_dump(body)}")
            return bool(resp.ok and isinstance(body, dict) and body.get("code") in (0, "0", None))
        except Exception as e:
            _log_gateway(f"feishu card_update failed: {e}")
        return False

    def send_card_reply(self, event=None, card_content="正在处理...", title="🤖 AI 助手", thinking=None, collapse_thinking=False, content_parts=None, interactive_actions=None):
        message_id = (event or {}).get("message_id")
        if not message_id:
            return None
        tenant_token = self._get_tenant_token()
        if not tenant_token:
            return None
        try:
            card_data = self._build_card_json(card_content or "正在处理...", title=title, thinking=thinking, collapse_thinking=collapse_thinking, content_parts=content_parts, interactive_actions=interactive_actions)
            content = json.dumps(card_data, ensure_ascii=False)
            resp = requests.post(
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
                headers={"Authorization": f"Bearer {tenant_token}"},
                json={"msg_type": "interactive", "content": content},
                timeout=5
            )
            body = None
            try:
                body = resp.json()
            except Exception:
                body = _truncate_text(getattr(resp, "text", ""))
            _log_gateway(f"feishu card_reply status={resp.status_code} ok={resp.ok} body={_safe_json_dump(body)}")
            if resp.ok and isinstance(body, dict) and body.get("code") in (0, "0", None):
                data = body.get("data") or {}
                return data.get("message_id")
            if isinstance(body, dict):
                code = body.get("code")
                msg = body.get("msg") or ""
                if code == 230099 or "parse card json err" in msg:
                    card_data_v1 = self._build_card_json_v1(card_content or "正在处理...", title=title, thinking=thinking, collapse_thinking=collapse_thinking)
                    fallback_content = json.dumps(card_data_v1, ensure_ascii=False)
                    fallback_resp = requests.post(
                        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
                        headers={"Authorization": f"Bearer {tenant_token}"},
                        json={"msg_type": "interactive", "content": fallback_content},
                        timeout=5
                    )
                    fallback_body = None
                    try:
                        fallback_body = fallback_resp.json()
                    except Exception:
                        fallback_body = _truncate_text(getattr(fallback_resp, "text", ""))
                    _log_gateway(f"feishu card_reply fallback status={fallback_resp.status_code} ok={fallback_resp.ok} body={_safe_json_dump(fallback_body)}")
                    if fallback_resp.ok and isinstance(fallback_body, dict) and fallback_body.get("code") in (0, "0", None):
                        data = (fallback_body.get("data") or {})
                        return data.get("message_id")
            return None
        except Exception as e:
            _log_gateway(f"feishu card_reply failed: {e}")
        return None

    def update_card_message(self, message_id, content, title="🤖 AI 助手", thinking=None, collapse_thinking=False, content_parts=None, interactive_actions=None):
        tenant_token = self._get_tenant_token()
        if not tenant_token:
            return False
        try:
            card_data = self._build_card_json(content or "生成中", title=title, thinking=thinking, collapse_thinking=collapse_thinking, content_parts=content_parts, interactive_actions=interactive_actions)
            payload = {"msg_type": "interactive", "content": json.dumps(card_data, ensure_ascii=False)}
            resp = requests.patch(
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
                headers={"Authorization": f"Bearer {tenant_token}"},
                json=payload,
                timeout=5
            )
            body = None
            try:
                body = resp.json()
            except Exception:
                body = _truncate_text(getattr(resp, "text", ""))
            _log_gateway(f"feishu card_edit status={resp.status_code} ok={resp.ok} body={_safe_json_dump(body)}")
            if resp.ok and isinstance(body, dict) and body.get("code") in (0, "0", None):
                return True
            if isinstance(body, dict):
                code = body.get("code")
                msg = body.get("msg") or ""
                if code == 230099 or "parse card json err" in msg:
                    card_data_v1 = self._build_card_json_v1(content or "生成中", title=title, thinking=thinking, collapse_thinking=collapse_thinking)
                    fallback_payload = {"msg_type": "interactive", "content": json.dumps(card_data_v1, ensure_ascii=False)}
                    fallback_resp = requests.patch(
                        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}",
                        headers={"Authorization": f"Bearer {tenant_token}"},
                        json=fallback_payload,
                        timeout=5
                    )
                    fallback_body = None
                    try:
                        fallback_body = fallback_resp.json()
                    except Exception:
                        fallback_body = _truncate_text(getattr(fallback_resp, "text", ""))
                    _log_gateway(f"feishu card_edit fallback status={fallback_resp.status_code} ok={fallback_resp.ok} body={_safe_json_dump(fallback_body)}")
                    return bool(fallback_resp.ok and isinstance(fallback_body, dict) and fallback_body.get("code") in (0, "0", None))
        except Exception as e:
            _log_gateway(f"feishu card_edit failed: {e}")
        return False

    def build_reply(self, text):
        return {"msg_type": "text", "content": {"text": text}}

    def send_message(self, text, event=None):
        message_id = (event or {}).get("message_id")
        _log_gateway(f"feishu send_message start message_id={message_id} text_len={len(text or '')}")
        if self.app_id and self.app_secret and message_id:
            tenant_token = self._get_tenant_token()
            if tenant_token:
                content = json.dumps({"text": text}, ensure_ascii=False)
                try:
                    reply_resp = requests.post(
                        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
                        headers={"Authorization": f"Bearer {tenant_token}"},
                        json={"msg_type": "text", "content": content},
                        timeout=5
                    )
                    reply_body = None
                    try:
                        reply_body = reply_resp.json()
                    except Exception:
                        reply_body = _truncate_text(getattr(reply_resp, "text", ""))
                    _log_gateway(f"feishu reply_resp status={reply_resp.status_code} ok={reply_resp.ok} body={_safe_json_dump(reply_body)}")
                except Exception as e:
                    _log_gateway(f"feishu reply request failed: {e}")
            return {"code": 0, "msg": "success"}
        return self.build_reply(text)

class SessionMapper:
    def __init__(self, chat_storage):
        self.chat_storage = chat_storage

    def get_or_create(self, provider, im_user_key):
        conversation_id = self.chat_storage.get_im_session(provider, im_user_key)
        if conversation_id:
            return conversation_id
        conversation_id = uuid.uuid4().hex
        self.chat_storage.upsert_im_session(provider, im_user_key, conversation_id)
        return conversation_id


def _extract_content(result):
    if not isinstance(result, dict):
        return ""
    content_parts = result.get("content_parts")
    if isinstance(content_parts, list) and content_parts:
        lines = []
        for part in content_parts:
            if not isinstance(part, dict):
                continue
            part_type = (part.get("type") or "").strip().lower()
            if part_type == "text":
                text_value = part.get("text") or ""
                if text_value.strip():
                    lines.append(text_value.strip())
            elif part_type == "file":
                file_name = part.get("name") or ""
                file_url = part.get("url") or ""
                file_path = part.get("path") or ""
                caption = part.get("caption") or ""
                link_value = file_url or file_path
                if file_name and link_value:
                    line = f"[文件] {file_name}: {link_value}"
                elif link_value:
                    line = f"[文件] {link_value}"
                elif file_name:
                    line = f"[文件] {file_name}"
                else:
                    line = "[文件]"
                if caption:
                    line += f" ({caption})"
                lines.append(line)
        merged = "\n".join([line for line in lines if line.strip()]).strip()
        if merged:
            return merged
    content = result.get("content") or ""
    generated_messages = result.get("generated_messages") or []
    if not (content or "").strip() and generated_messages:
        for msg in reversed(generated_messages):
            if msg.get("role") == "assistant":
                msg_content = msg.get("content") or ""
                msg_parts = msg.get("content_parts")
                if isinstance(msg_parts, list) and msg_parts:
                    parts_lines = []
                    for part in msg_parts:
                        if isinstance(part, dict) and (part.get("type") or "").strip().lower() == "text":
                            part_text = part.get("text") or ""
                            if part_text.strip():
                                parts_lines.append(part_text.strip())
                    merged_parts = "\n".join(parts_lines).strip()
                    if merged_parts:
                        return merged_parts
                if msg_content.strip():
                    return msg_content
    if not (content or "").strip():
        return ""
    return content


def _has_effective_output(text):
    value = (text or "").strip()
    if not value:
        return False
    placeholders = {
        "任务已处理完成，请查看工具执行结果。",
        "处理完成",
    }
    return value not in placeholders


def _resolve_streaming_config(config_manager):
    cfg = {
        "min_chars": 1200,
        "min_interval": 2.0,
        "answer_min_chars": 1000,
        "answer_min_interval": 1.8,
        "thinking_min_chars": 1400,
        "thinking_min_interval": 2.2,
        "backoff_min_interval": 2.8,
    }
    if not config_manager:
        return cfg
    raw_chars = config_manager.get("feishu_stream_min_chars", cfg["min_chars"])
    raw_interval = config_manager.get("feishu_stream_min_interval", cfg["min_interval"])
    raw_answer_chars = config_manager.get("feishu_stream_answer_min_chars", cfg["answer_min_chars"])
    raw_answer_interval = config_manager.get("feishu_stream_answer_min_interval", cfg["answer_min_interval"])
    raw_think_chars = config_manager.get("feishu_stream_thinking_min_chars", cfg["thinking_min_chars"])
    raw_think_interval = config_manager.get("feishu_stream_thinking_min_interval", cfg["thinking_min_interval"])
    raw_backoff_interval = config_manager.get("feishu_stream_backoff_min_interval", cfg["backoff_min_interval"])
    try:
        cfg["min_chars"] = int(raw_chars)
    except Exception:
        cfg["min_chars"] = 1200
    try:
        cfg["min_interval"] = float(raw_interval)
    except Exception:
        cfg["min_interval"] = 2.0
    try:
        cfg["answer_min_chars"] = int(raw_answer_chars)
    except Exception:
        cfg["answer_min_chars"] = cfg["min_chars"]
    try:
        cfg["answer_min_interval"] = float(raw_answer_interval)
    except Exception:
        cfg["answer_min_interval"] = cfg["min_interval"]
    try:
        cfg["thinking_min_chars"] = int(raw_think_chars)
    except Exception:
        cfg["thinking_min_chars"] = max(cfg["answer_min_chars"], cfg["min_chars"])
    try:
        cfg["thinking_min_interval"] = float(raw_think_interval)
    except Exception:
        cfg["thinking_min_interval"] = max(cfg["answer_min_interval"], cfg["min_interval"])
    try:
        cfg["backoff_min_interval"] = float(raw_backoff_interval)
    except Exception:
        cfg["backoff_min_interval"] = 2.8
    cfg["min_chars"] = max(300, min(cfg["min_chars"], 5000))
    cfg["answer_min_chars"] = max(300, min(cfg["answer_min_chars"], 5000))
    cfg["thinking_min_chars"] = max(cfg["answer_min_chars"], min(cfg["thinking_min_chars"], 5000))
    cfg["min_interval"] = max(0.8, min(cfg["min_interval"], 8.0))
    cfg["answer_min_interval"] = max(0.8, min(cfg["answer_min_interval"], 8.0))
    cfg["thinking_min_interval"] = max(cfg["answer_min_interval"], min(cfg["thinking_min_interval"], 8.0))
    cfg["backoff_min_interval"] = max(cfg["thinking_min_interval"], min(cfg["backoff_min_interval"], 12.0))
    return cfg


def _stream_im_response(conversation_id, event, provider, daemon_client, workspace_dir, config_manager=None):
    pending_text = ""
    total_text = ""
    pending_thinking = ""
    total_thinking = ""
    sent_any = False
    last_send_time = time.time()
    stream_cfg = _resolve_streaming_config(config_manager)
    card_message_id = None
    use_card = False
    card_attempted = False
    think_state = {"in_think": False, "buffer": ""}
    recovered_once = False
    update_fail_count = 0
    tool_events = []
    file_parts = []
    tool_call_index = {}
    model_input_text = _build_feishu_model_input(event)
    def _effective_interval(base_interval):
        if update_fail_count >= 2:
            return max(base_interval, stream_cfg["backoff_min_interval"])
        return base_interval
    def _append_file_parts(candidates):
        if not isinstance(candidates, list):
            return
        existing_keys = set()
        for item in file_parts:
            key = (item.get("url") or item.get("path") or "").strip().lower()
            if key:
                existing_keys.add(key)
        for item in candidates:
            if not isinstance(item, dict):
                continue
            key = (item.get("url") or item.get("path") or "").strip().lower()
            if not key or key in existing_keys:
                continue
            file_parts.append(item)
            existing_keys.add(key)
    def _compose_content_parts(content_text):
        parts = []
        if (content_text or "").strip():
            parts.append({"type": "text", "text": content_text})
        if tool_events:
            parts.extend(tool_events[-16:])
        if file_parts:
            parts.extend(file_parts[-8:])
        return parts
    def _attach_result_parts(result_dict):
        if not isinstance(result_dict, dict):
            return result_dict
        existing = result_dict.get("content_parts")
        merged = []
        if isinstance(existing, list):
            merged.extend(existing)
        merged.extend(_compose_content_parts(total_text or result_dict.get("content") or ""))
        result_dict["content_parts"] = merged
        return result_dict
    def _flush_card(collapse_thinking=False):
        nonlocal pending_text, pending_thinking, sent_any, last_send_time, update_fail_count
        if not (use_card and card_message_id):
            return False
        content = total_text or "正在处理..."
        thinking = _truncate_text(total_thinking, limit=2000)
        content_parts = _compose_content_parts(content)
        updated = provider.update_card_message(card_message_id, content, thinking=thinking, collapse_thinking=collapse_thinking, content_parts=content_parts)
        if updated:
            sent_any = True
            update_fail_count = 0
            with _CARD_CONTEXT_LOCK:
                ctx = _CARD_CONTEXT.get(card_message_id)
                if isinstance(ctx, dict):
                    ctx["last_content"] = content
                    ctx["last_thinking"] = thinking
                    ctx["content_parts"] = content_parts
                    ctx["tool_events"] = [p for p in content_parts if isinstance(p, dict) and (p.get("type") or "").lower() == "tool_event"]
                    ctx["updated_at"] = time.time()
        else:
            update_fail_count += 1
        pending_text = ""
        pending_thinking = ""
        last_send_time = time.time()
        return bool(updated)
    if hasattr(provider, "send_card_reply") and hasattr(provider, "update_card_message"):
        card_attempted = True
        card_message_id = provider.send_card_reply(event, card_content="正在处理...", title="🤖 AI 助手", thinking="思考中...", content_parts=[{"type": "text", "text": "正在处理..."}])
        if card_message_id:
            use_card = True
            with _CARD_CONTEXT_LOCK:
                _CARD_CONTEXT[card_message_id] = {
                    "conversation_id": conversation_id,
                    "event": event,
                    "workspace_dir": workspace_dir,
                    "updated_at": time.time(),
                    "tool_events": [],
                    "content_parts": [],
                    "last_thinking": "",
                    "last_content": "",
                    "awaiting_feedback": False,
                    "thinking_expanded": False
                }
    try:
        for msg in daemon_client.send_message_stream(conversation_id, model_input_text, workspace_dir):
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "content":
                raw_delta = msg.get("delta") or ""
                delta, think_state = _strip_think_blocks(raw_delta, think_state)
                if delta:
                    pending_text += delta
                    total_text += delta
                    now = time.time()
                    answer_interval = _effective_interval(stream_cfg["answer_min_interval"])
                    if len(pending_text) >= stream_cfg["answer_min_chars"] or len(pending_thinking) >= stream_cfg["answer_min_chars"] or (now - last_send_time) >= answer_interval:
                        if use_card:
                            _flush_card(collapse_thinking=False)
                        else:
                            pending_text = ""
                            pending_thinking = ""
                            last_send_time = now
            elif msg.get("type") == "thinking":
                thinking_delta = msg.get("delta") or ""
                if thinking_delta:
                    pending_thinking += thinking_delta
                    total_thinking += thinking_delta
                    now = time.time()
                    think_interval = _effective_interval(stream_cfg["thinking_min_interval"])
                    if len(pending_thinking) >= stream_cfg["thinking_min_chars"] or (now - last_send_time) >= think_interval:
                        if use_card:
                            _flush_card(collapse_thinking=False)
            elif msg.get("type") == "interaction_request":
                data = msg.get("data") or {}
                request_id = data.get("request_id")
                if request_id:
                    hint = _build_interaction_hint(data)
                    if use_card and card_message_id and hasattr(provider, "update_card_message"):
                        provider.update_card_message(
                            card_message_id,
                            hint,
                            thinking=_truncate_text(total_thinking, limit=2000),
                            collapse_thinking=False,
                            content_parts=[{"type": "text", "text": hint}]
                        )
                    else:
                        provider.send_card_reply(event, card_content=hint, title="🤖 AI 助手")
            elif msg.get("type") == "tool_call":
                data = msg.get("data") or {}
                tool_id = data.get("id") or ""
                tool_name = data.get("name") or "tool"
                args = data.get("args") or {}
                arg_preview = ""
                try:
                    arg_preview = json.dumps(args, ensure_ascii=False)[:240]
                except Exception:
                    arg_preview = str(args)[:240]
                if tool_id:
                    tool_call_index[tool_id] = {
                        "tool_name": tool_name,
                        "args_preview": arg_preview,
                        "start_time": time.time(),
                    }
                tool_events.append(
                    {
                        "type": "tool_event",
                        "tool_id": tool_id,
                        "tool_name": tool_name,
                        "status": "running",
                        "summary": arg_preview
                    }
                )
            elif msg.get("type") == "tool_result":
                data = msg.get("data") or {}
                tool_id = data.get("id") or ""
                tool_meta = tool_call_index.get(tool_id, {})
                tool_name = data.get("name") or tool_meta.get("tool_name") or "tool"
                result_text = data.get("result") or ""
                meta = data.get("meta") or {}
                if not isinstance(result_text, str):
                    try:
                        result_text = json.dumps(result_text, ensure_ascii=False)
                    except Exception:
                        result_text = str(result_text)
                duration_value = meta.get("duration")
                duration_text = ""
                try:
                    if duration_value is not None:
                        duration_text = f" ({float(duration_value):.2f}s)"
                except Exception:
                    duration_text = ""
                args_preview = tool_meta.get("args_preview") or ""
                summary_text = _truncate_text(result_text, limit=260)
                if args_preview:
                    summary_text = f"args={args_preview} | result={summary_text}"
                if duration_text:
                    summary_text = f"{summary_text}{duration_text}"
                tool_events.append(
                    {
                        "type": "tool_event",
                        "tool_id": tool_id,
                        "tool_name": tool_name,
                        "status": "completed",
                        "summary": summary_text,
                        "duration": meta.get("duration")
                    }
                )
                payload = data.get("result_obj")
                if tool_name == "publish_artifacts":
                    payload = _extract_publish_artifacts_payload(payload or result_text)
                    if payload:
                        structured_files = []
                        for part in payload.get("content_parts") or []:
                            part_type = (part.get("type") or "").strip().lower()
                            if part_type == "tool_event":
                                tool_events.append(part)
                            elif part_type == "file":
                                structured_files.append(part)
                        if structured_files:
                            _append_file_parts(structured_files)
            elif msg.get("type") == "final":
                if pending_text or pending_thinking:
                    _flush_card(collapse_thinking=False)
                final_result = msg.get("result") or {"error": "No response"}
                final_result = _attach_result_parts(final_result)
                return final_result, total_text, pending_text, total_thinking, sent_any, card_message_id, use_card, card_attempted
            elif msg.get("type") == "error":
                error_text = msg.get("error") or "Daemon error"
                if (not recovered_once) and _is_context_overflow_error(error_text):
                    recovered_once = True
                    retry_resp = daemon_client.send_message(conversation_id, model_input_text, workspace_dir)
                    retry_result = (retry_resp or {}).get("result") if isinstance(retry_resp, dict) else None
                    if retry_result:
                        retry_result = _attach_result_parts(retry_result)
                        return retry_result, total_text, pending_text, total_thinking, sent_any, card_message_id, use_card, card_attempted
                return {"error": msg.get("error") or "Daemon error"}, total_text, pending_text, total_thinking, sent_any, card_message_id, use_card, card_attempted
            elif msg.get("status") == "error":
                error_text = msg.get("error") or "Daemon error"
                if (not recovered_once) and _is_context_overflow_error(error_text):
                    recovered_once = True
                    retry_resp = daemon_client.send_message(conversation_id, model_input_text, workspace_dir)
                    retry_result = (retry_resp or {}).get("result") if isinstance(retry_resp, dict) else None
                    if retry_result:
                        retry_result = _attach_result_parts(retry_result)
                        return retry_result, total_text, pending_text, total_thinking, sent_any, card_message_id, use_card, card_attempted
                return {"error": msg.get("error") or "Daemon error"}, total_text, pending_text, total_thinking, sent_any, card_message_id, use_card, card_attempted
            elif msg.get("status") == "ok" and "result" in msg:
                ok_result = msg.get("result") or {"error": "No response"}
                ok_result = _attach_result_parts(ok_result)
                return ok_result, total_text, pending_text, total_thinking, sent_any, card_message_id, use_card, card_attempted
    except Exception as e:
        return {"error": str(e)}, total_text, pending_text, total_thinking, sent_any, card_message_id, use_card, card_attempted
    return {"error": "Daemon stream closed"}, total_text, pending_text, total_thinking, sent_any, card_message_id, use_card, card_attempted


def _handle_im_event(payload, provider, session_mapper, config_manager, daemon_client):
    _log_gateway(f"feishu handle_im_event payload={_safe_json_dump(payload)}")
    event = provider.parse_event(payload)
    if not event:
        _log_gateway("feishu handle_im_event ignored: parse_event returned None")
        return None
    if "challenge" in event:
        _log_gateway("feishu handle_im_event ignored: challenge")
        return None
    event_type = event.get("event_type")
    if event_type == "card.action.trigger" or event_type == "card.action.trigger_v1":
        action_data = event.get("event") or {}
        action_event_id = action_data.get("event_id") or (action_data.get("header") or {}).get("event_id") or event.get("event_id")
        action_payload = action_data.get("action") or {}
        action_value = action_payload.get("value") if isinstance(action_payload, dict) else {}
        action_name = ""
        if isinstance(action_value, dict):
            action_name = action_value.get("action") or action_value.get("name") or ""
        if not action_name:
            action_name = action_data.get("action_name") or "unknown"
        dedup_key = f"{action_event_id}:{action_name}"
        if _seen_action(dedup_key):
            _log_gateway(f"feishu card action ignored by dedup dedup_key={dedup_key}")
            return None
        open_message_id = None
        if isinstance(action_data, dict):
            open_message_id = action_data.get("open_message_id")
            if not open_message_id:
                operator = action_data.get("operator") or {}
                open_message_id = operator.get("open_message_id")
        if not open_message_id and isinstance(action_payload, dict):
            open_message_id = action_payload.get("open_message_id")
        _log_gateway(f"feishu card action received action={action_name} message_id={open_message_id} event_id={action_event_id}")
        if open_message_id and hasattr(provider, "update_card_message"):
            with _CARD_CONTEXT_LOCK:
                ctx = _CARD_CONTEXT.get(open_message_id) or {}
            conversation_id = ctx.get("conversation_id")
            thinking_text = ctx.get("last_thinking") or ""
            last_content = ctx.get("last_content") or "处理中..."
            content_parts = ctx.get("content_parts") if isinstance(ctx.get("content_parts"), list) else [{"type": "text", "text": last_content}]
            actions_running = [
                {"name": "toggle_thinking", "label": "收起思考过程"},
                {"name": "stop", "label": "停止生成"},
                {"name": "detail", "label": "工具详情"},
                {"name": "feedback", "label": "反馈"},
            ]
            if action_name == "stop":
                if conversation_id:
                    try:
                        daemon_client.stop_session(conversation_id)
                    except Exception:
                        pass
                provider.update_card_message(open_message_id, "已停止当前任务。", thinking=thinking_text, collapse_thinking=False, content_parts=content_parts, interactive_actions=actions_running)
            elif action_name == "toggle_thinking":
                collapse = bool(ctx.get("thinking_expanded"))
                with _CARD_CONTEXT_LOCK:
                    live_ctx = _CARD_CONTEXT.get(open_message_id)
                    if isinstance(live_ctx, dict):
                        live_ctx["thinking_expanded"] = not collapse
                toggle_actions = [
                    {"name": "toggle_thinking", "label": "收起思考过程" if not collapse else "展开思考过程"},
                    {"name": "stop", "label": "停止生成"},
                    {"name": "detail", "label": "工具详情"},
                    {"name": "feedback", "label": "反馈"},
                ]
                provider.update_card_message(open_message_id, last_content, thinking=thinking_text, collapse_thinking=collapse, content_parts=content_parts, interactive_actions=toggle_actions)
            elif action_name == "detail":
                tool_events = [p for p in content_parts if isinstance(p, dict) and (p.get("type") or "").lower() == "tool_event"]
                lines = []
                for ev in tool_events[-10:]:
                    lines.append(f"- {ev.get('tool_name') or 'tool'} [{ev.get('status') or 'unknown'}]: {_truncate_text(ev.get('summary') or '', 120)}")
                detail_text = "工具详情暂无记录。" if not lines else ("工具详情\n" + "\n".join(lines))
                provider.update_card_message(open_message_id, detail_text, thinking=thinking_text, collapse_thinking=False, content_parts=content_parts, interactive_actions=actions_running)
            elif action_name == "feedback":
                with _CARD_CONTEXT_LOCK:
                    live_ctx = _CARD_CONTEXT.get(open_message_id)
                    if isinstance(live_ctx, dict):
                        live_ctx["awaiting_feedback"] = True
                provider.update_card_message(open_message_id, "请直接回复你对当前结果的反馈内容，我会转给 AI 并继续优化。", thinking=thinking_text, collapse_thinking=False, content_parts=content_parts, interactive_actions=actions_running)
        return None
    if event_type != "im.message.receive_v1":
        _log_gateway(f"feishu handle_im_event ignored: event_type={event.get('event_type')}")
        return None
    if event.get("sender_type") and event.get("sender_type") != "user":
        _log_gateway(f"feishu handle_im_event ignored: sender_type={event.get('sender_type')}")
        return None
    if event.get("message_type") and event.get("message_type") != "text":
        _log_gateway(f"feishu handle_im_event ignored: message_type={event.get('message_type')}")
        return None
    if not event.get("text"):
        _log_gateway("feishu handle_im_event ignored: empty text")
        return None
    dedup_key = event.get("message_id") or event.get("event_id")
    if _seen_message(dedup_key):
        _log_gateway(f"feishu handle_im_event ignored: dedup_key={dedup_key}")
        return None
    try:
        config_manager.load_config()
    except Exception as e:
        _log_gateway(f"feishu load_config failed error={e}")
    workspace_dir = config_manager.get("default_workspace", "")
    if not config_manager.get_god_mode() and not workspace_dir:
        _log_gateway("feishu handle_im_event blocked: workspace not configured")
        provider.send_card_reply(event, card_content="请先在桌面端选择默认工作区（未开启上帝模式，需在工作区内操作）。", title="🤖 AI 助手")
        return None
    try:
        chat_id = (event.get("chat_id") or "").strip()
        user_id = (event.get("user_id") or "").strip()
        if chat_id:
            config_manager.set("feishu_receive_id_type", "chat_id")
            config_manager.set("feishu_receive_id", chat_id)
        elif user_id:
            config_manager.set("feishu_receive_id_type", "open_id")
            config_manager.set("feishu_receive_id", user_id)
    except Exception:
        pass
    date_key = resolve_date_key(event.get("create_time"))
    session_key = build_im_session_key(event["user_id"], event.get("chat_id") or "", date_key)
    conversation_id = session_mapper.get_or_create("feishu", session_key)
    pending_resp = daemon_client.get_pending_interaction(conversation_id)
    pending_interaction = (pending_resp or {}).get("pending") if isinstance(pending_resp, dict) else None
    if pending_interaction:
        parsed_payload, valid, _ = parse_interaction_reply(pending_interaction, event.get("text") or "")
        request_id = pending_interaction.get("request_id")
        if not valid:
            provider.send_card_reply(
                event,
                card_content=_build_interaction_hint(pending_interaction),
                title="🤖 AI 助手"
            )
            return None
        try:
            response = None
            for _ in range(3):
                response = daemon_client.respond_interaction(request_id, parsed_payload)
                if isinstance(response, dict):
                    break
                time.sleep(0.25)
            resolved = isinstance(response, dict) and response.get("status") == "ok" and bool(response.get("resolved"))
            if resolved:
                ack_text = "已收到输入，继续处理中，请稍候…"
                if pending_interaction.get("kind") == "approval":
                    ack_text = "已收到确认：是，继续处理中，请稍候…" if parsed_payload.get("approved") else "已收到确认：否，继续处理中，请稍候…"
                elif pending_interaction.get("kind") == "text":
                    ack_text = f"已收到输入：{parsed_payload.get('text') or ''}\n继续处理中，请稍候…"
                elif parsed_payload.get("selected_options"):
                    ack_text = f"已收到选择：{', '.join(parsed_payload.get('selected_options') or [])}\n继续处理中，请稍候…"
                provider.send_card_reply(event, card_content=ack_text, title="🤖 AI 助手")
            else:
                status_text = ""
                if isinstance(response, dict):
                    status_text = response.get("error") or ("resolved=false" if response.get("status") == "ok" else response.get("status") or "")
                provider.send_card_reply(
                    event,
                    card_content=f"交互回传未生效（{status_text or '通道异常'}）。请重新回复。",
                    title="🤖 AI 助手"
                )
        except Exception as e:
            provider.send_card_reply(event, card_content=f"交互回传失败：{e}", title="🤖 AI 助手")
        return None
    event = dict(event)
    event["text"] = _consume_feedback_prefix(conversation_id, event.get("text") or "")
    _log_gateway(f"feishu session mapped conversation_id={conversation_id} session_key={session_key}")
    _log_gateway(f"feishu daemon request conversation_id={conversation_id} text_len={len(event.get('text') or '')} workspace={workspace_dir}")
    result, total_text, pending_text, total_thinking, sent_any, card_message_id, use_card, card_attempted = _stream_im_response(
        conversation_id, event, provider, daemon_client, workspace_dir, config_manager=config_manager
    )
    if result.get("error"):
        _log_gateway(f"feishu daemon stream error response={_safe_json_dump(result)}")
        if card_message_id:
            provider.update_card_message(card_message_id, f"⚠️ {result.get('error')}", thinking=_truncate_text(total_thinking, limit=2000), collapse_thinking=True)
        else:
            provider.send_card_reply(event, card_content=f"⚠️ {result.get('error')}", title="🤖 AI 助手", thinking=_truncate_text(total_thinking, limit=2000), collapse_thinking=True)
        return None
    if pending_text:
        pending_text = ""
    output = _extract_content(result)
    output_parts = result.get("content_parts") if isinstance(result, dict) else None
    if not isinstance(output_parts, list):
        output_parts = [{"type": "text", "text": output or ""}]
    if total_text.strip():
        output = total_text
        output_parts = [{"type": "text", "text": output}] + [p for p in output_parts if isinstance(p, dict) and (p.get("type") or "").lower() != "text"]
    has_output = _has_effective_output(output)
    display_output = output if has_output else " "
    final_actions = [
        {"name": "toggle_thinking", "label": "展开思考过程"},
        {"name": "stop", "label": "停止生成"},
        {"name": "detail", "label": "工具详情"},
        {"name": "feedback", "label": "反馈"},
    ]
    if use_card and card_message_id:
        final_ok = provider.update_card_message(
            card_message_id,
            display_output,
            thinking=_truncate_text(total_thinking, limit=2000),
            collapse_thinking=True,
            content_parts=output_parts,
            interactive_actions=final_actions
        )
        with _CARD_CONTEXT_LOCK:
            ctx = _CARD_CONTEXT.get(card_message_id)
            if isinstance(ctx, dict):
                ctx["last_content"] = display_output
                ctx["last_thinking"] = _truncate_text(total_thinking, limit=2000)
                ctx["content_parts"] = output_parts
                ctx["tool_events"] = [p for p in output_parts if isinstance(p, dict) and (p.get("type") or "").lower() == "tool_event"]
                ctx["updated_at"] = time.time()
                ctx["thinking_expanded"] = False
        if not final_ok:
            provider.send_card_reply(
                event,
                card_content=display_output,
                title="🤖 AI 助手",
                thinking=_truncate_text(total_thinking, limit=2000),
                collapse_thinking=True,
                content_parts=output_parts,
                interactive_actions=final_actions
            )
        return None
    if (not sent_any and has_output) or ((not total_text.strip()) and has_output):
        _log_gateway(f"feishu daemon ok result={_safe_json_dump(result)} output_len={len(output or '')}")
        if hasattr(provider, "send_card_reply"):
            if card_message_id:
                return None
            if card_attempted:
                return None
            message_id = provider.send_card_reply(event, card_content=display_output, title="🤖 AI 助手", thinking=_truncate_text(total_thinking, limit=2000), collapse_thinking=True, content_parts=output_parts, interactive_actions=final_actions)
            if message_id:
                return None
        provider.send_card_reply(event, card_content=display_output, title="🤖 AI 助手", thinking=_truncate_text(total_thinking, limit=2000), collapse_thinking=True, content_parts=output_parts, interactive_actions=final_actions)
    return None


def build_context():
    config_manager = ConfigManager()
    history_dir = config_manager.get_chat_history_dir()
    db_path = os.path.join(history_dir, "chat_history.sqlite")
    chat_storage = ChatStorage(db_path)
    session_mapper = SessionMapper(chat_storage)

    daemon_host = config_manager.get("daemon_host", DEFAULT_HOST)
    daemon_port = config_manager.get("daemon_port", DEFAULT_PORT)
    daemon_client = DaemonClient(daemon_host, daemon_port)

    provider = FeishuProvider(config_manager)
    return config_manager, session_mapper, daemon_client, provider


def _start_feishu_long_connection(context):
    if not _load_lark_sdk():
        _log_gateway("lark_oapi unavailable")
        return None
    config_manager, session_mapper, daemon_client, provider = context
    if not provider or not provider.app_id or not provider.app_secret:
        _log_gateway("feishu app_id/app_secret missing")
        return None
    enabled = config_manager.get("feishu_long_connection", True)
    if isinstance(enabled, str) and enabled.strip().lower() in ("0", "false", "no"):
        _log_gateway("feishu long connection disabled by config")
        return None
    if enabled is False:
        _log_gateway("feishu long connection disabled by config")
        return None

    def on_message_receive(data):
        payload = json.loads(lark.JSON.marshal(data))
        _log_gateway(f"feishu ws message received payload={_safe_json_dump(payload)}")
        threading.Thread(
            target=_handle_im_event,
            args=(payload, provider, session_mapper, config_manager, daemon_client),
            daemon=True
        ).start()
        return None

    handler = EventDispatcherHandler.builder("", "", larkcore.LogLevel.INFO)
    handler = handler.register_p2_im_message_receive_v1(on_message_receive)
    optional_registers = [
        "register_p2_card_action_trigger",
        "register_p2_card_action_trigger_v1",
    ]
    for method_name in optional_registers:
        register_method = getattr(handler, method_name, None)
        if callable(register_method):
            try:
                handler = register_method(on_message_receive)
                _log_gateway(f"feishu callback registered: {method_name}")
            except Exception as e:
                _log_gateway(f"feishu callback register failed method={method_name} error={e}")
    handler = handler.build()
    client = larkws.Client(
        provider.app_id,
        provider.app_secret,
        log_level=larkcore.LogLevel.INFO,
        event_handler=handler
    )
    _log_gateway("feishu long connection starting")
    try:
        client.start()
    except Exception as e:
        _log_gateway(f"feishu long connection failed: {e}")
    return None


def run():
    context = build_context()
    _log_gateway("im_gateway start")
    thread = threading.Thread(target=_start_feishu_long_connection, args=(context,), daemon=True)
    thread.start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    run()
