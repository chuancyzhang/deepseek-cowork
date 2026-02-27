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

_RECENT_MESSAGE_IDS = {}
_RECENT_LOCK = threading.Lock()
_MESSAGE_ID_TTL = 30

def _log_gateway(message):
    try:
        log_dir = get_app_data_dir()
        log_path = os.path.join(log_dir, "im_gateway.log")
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")
    except Exception:
        pass

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

    def _build_card_json(self, content, title="🤖 AI 助手"):
        return {
            "schema": "2.0",
            "config": {"update_multi": True, "streaming_mode": True, "summary": {"content": "生成中"}},
            "header": {"title": {"tag": "plain_text", "content": title}},
            "body": {"elements": [{"tag": "markdown", "content": content}]}
        }

    def _build_card_json_v1(self, content, title="🤖 AI 助手"):
        return {
            "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": title}},
            "elements": [{"tag": "markdown", "content": content}]
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

    def send_card_reply(self, event=None, card_content="正在处理...", title="🤖 AI 助手"):
        message_id = (event or {}).get("message_id")
        if not message_id:
            return None
        tenant_token = self._get_tenant_token()
        if not tenant_token:
            return None
        try:
            card_data = self._build_card_json(card_content or "正在处理...", title=title)
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
                    card_data_v1 = self._build_card_json_v1(card_content or "正在处理...", title=title)
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

    def update_card_message(self, message_id, content, title="🤖 AI 助手"):
        tenant_token = self._get_tenant_token()
        if not tenant_token:
            return False
        try:
            card_data = self._build_card_json(content or "生成中", title=title)
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
                    card_data_v1 = self._build_card_json_v1(content or "生成中", title=title)
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
        return "⚠️ 未收到有效回复，请重试或查看守护进程状态。"
    content = result.get("content") or ""
    generated_messages = result.get("generated_messages") or []
    if not (content or "").strip() and generated_messages:
        for msg in reversed(generated_messages):
            if msg.get("role") == "assistant":
                msg_content = msg.get("content") or ""
                if msg_content.strip():
                    return msg_content
    if not (content or "").strip():
        return "⚠️ 未收到有效回复，请重试或查看守护进程状态。"
    return content


def _stream_im_response(conversation_id, event, provider, daemon_client, workspace_dir):
    pending_text = ""
    total_text = ""
    sent_any = False
    last_send_time = time.time()
    min_chars = 200
    min_interval = 0.8
    card_message_id = None
    use_card = False
    card_attempted = False
    think_state = {"in_think": False, "buffer": ""}
    if hasattr(provider, "send_card_reply") and hasattr(provider, "update_card_message"):
        card_attempted = True
        card_message_id = provider.send_card_reply(event, card_content="正在处理...", title="🤖 AI 助手")
        if card_message_id:
            use_card = True
    try:
        for msg in daemon_client.send_message_stream(conversation_id, event["text"], workspace_dir):
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "content":
                raw_delta = msg.get("delta") or ""
                delta, think_state = _strip_think_blocks(raw_delta, think_state)
                if delta:
                    pending_text += delta
                    total_text += delta
                    now = time.time()
                    if len(pending_text) >= min_chars or (now - last_send_time) >= min_interval:
                        if use_card and card_message_id:
                            content = total_text or "正在处理..."
                            provider.update_card_message(card_message_id, content)
                        if use_card:
                            pending_text = ""
                            last_send_time = now
                        else:
                            pending_text = ""
                            last_send_time = now
            elif msg.get("type") == "confirm_request":
                data = msg.get("data") or {}
                confirm_id = data.get("id")
                message = data.get("message")
                if confirm_id and message:
                    daemon_client.confirm_response(confirm_id, False)
            elif msg.get("type") == "final":
                return msg.get("result") or {"error": "No response"}, total_text, pending_text, sent_any, card_message_id, use_card, card_attempted
            elif msg.get("type") == "error":
                return {"error": msg.get("error") or "Daemon error"}, total_text, pending_text, sent_any, card_message_id, use_card, card_attempted
            elif msg.get("status") == "error":
                return {"error": msg.get("error") or "Daemon error"}, total_text, pending_text, sent_any, card_message_id, use_card, card_attempted
            elif msg.get("status") == "ok" and "result" in msg:
                return msg.get("result") or {"error": "No response"}, total_text, pending_text, sent_any, card_message_id, use_card, card_attempted
    except Exception as e:
        return {"error": str(e)}, total_text, pending_text, sent_any, card_message_id, use_card, card_attempted
    return {"error": "Daemon stream closed"}, total_text, pending_text, sent_any, card_message_id, use_card, card_attempted


def _handle_im_event(payload, provider, session_mapper, config_manager, daemon_client):
    _log_gateway(f"feishu handle_im_event payload={_safe_json_dump(payload)}")
    event = provider.parse_event(payload)
    if not event:
        _log_gateway("feishu handle_im_event ignored: parse_event returned None")
        return None
    if "challenge" in event:
        _log_gateway("feishu handle_im_event ignored: challenge")
        return None
    if event.get("event_type") != "im.message.receive_v1":
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
    except Exception:
        pass
    workspace_dir = config_manager.get("default_workspace", "")
    if not config_manager.get_god_mode() and not workspace_dir:
        _log_gateway("feishu handle_im_event blocked: workspace not configured")
        provider.send_card_reply(event, card_content="请先在桌面端选择默认工作区（未开启上帝模式，需在工作区内操作）。", title="🤖 AI 助手")
        return None
    unique_key = event.get("message_id") or event.get("event_id") or event.get("create_time") or uuid.uuid4().hex
    session_key = f"{event['user_id']}:{event.get('chat_id') or ''}:{unique_key}"
    conversation_id = session_mapper.get_or_create("feishu", session_key)
    _log_gateway(f"feishu session mapped conversation_id={conversation_id} session_key={session_key}")
    _log_gateway(f"feishu daemon request conversation_id={conversation_id} text_len={len(event.get('text') or '')} workspace={workspace_dir}")
    result, total_text, pending_text, sent_any, card_message_id, use_card, card_attempted = _stream_im_response(
        conversation_id, event, provider, daemon_client, workspace_dir
    )
    if result.get("error"):
        _log_gateway(f"feishu daemon stream error response={_safe_json_dump(result)}")
        if card_message_id:
            provider.update_card_message(card_message_id, f"⚠️ {result.get('error')}")
        else:
            provider.send_card_reply(event, card_content=f"⚠️ {result.get('error')}", title="🤖 AI 助手")
        return None
    if pending_text:
        if use_card and card_message_id:
            provider.update_card_message(card_message_id, (total_text + pending_text) or "处理完成")
            pending_text = ""
        if pending_text:
            pending_text = ""
    output = _extract_content(result)
    if use_card and card_message_id:
        provider.update_card_message(card_message_id, output or "处理完成")
        return None
    if (not sent_any and output) or ((not total_text.strip()) and output):
        _log_gateway(f"feishu daemon ok result={_safe_json_dump(result)} output_len={len(output or '')}")
        if hasattr(provider, "send_card_reply"):
            if card_message_id:
                return None
            if card_attempted:
                return None
            message_id = provider.send_card_reply(event, card_content=output, title="🤖 AI 助手")
            if message_id:
                return None
        provider.send_card_reply(event, card_content=output, title="🤖 AI 助手")
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
