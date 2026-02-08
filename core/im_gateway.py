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
        except Exception:
            return None

    def parse_event(self, payload):
        if isinstance(payload, dict) and payload.get("type") == "url_verification":
            if self.verification_token and payload.get("token") != self.verification_token:
                return None
            return {"challenge": payload.get("challenge", "")}
        data = payload
        if isinstance(payload, dict) and payload.get("encrypt"):
            data = self._decrypt(payload.get("encrypt"))
        if not isinstance(data, dict):
            return None
        header = data.get("header") or {}
        if self.verification_token and header.get("token") != self.verification_token:
            return None
        if self.app_id and header.get("app_id") != self.app_id:
            return None
        if "challenge" in data:
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
                return {"event_type": event_type, "event": data}
            return None
        return {
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

    def build_reply(self, text):
        return {"msg_type": "text", "content": {"text": text}}

    def send_message(self, text, event=None):
        message_id = (event or {}).get("message_id")
        if self.app_id and self.app_secret and message_id:
            token_resp = requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=5
            )
            if token_resp.ok:
                token_data = token_resp.json() or {}
                tenant_token = token_data.get("tenant_access_token")
                if tenant_token:
                    content = json.dumps({"text": text}, ensure_ascii=False)
                    requests.post(
                        f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply",
                        headers={"Authorization": f"Bearer {tenant_token}"},
                        json={"msg_type": "text", "content": content},
                        timeout=5
                    )
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


def _handle_im_event(payload, provider, session_mapper, config_manager, daemon_client):
    event = provider.parse_event(payload)
    if not event:
        return None
    if "challenge" in event:
        return None
    if event.get("event_type") != "im.message.receive_v1":
        return None
    if event.get("sender_type") and event.get("sender_type") != "user":
        return None
    if event.get("message_type") and event.get("message_type") != "text":
        return None
    if not event.get("text"):
        return None
    dedup_key = event.get("message_id") or event.get("event_id")
    if _seen_message(dedup_key):
        return None
    try:
        config_manager.load_config()
    except Exception:
        pass
    workspace_dir = config_manager.get("default_workspace", "")
    if not config_manager.get_god_mode() and not workspace_dir:
        provider.send_message("请先在桌面端选择默认工作区（未开启上帝模式，需在工作区内操作）。", event)
        return None
    unique_key = event.get("message_id") or event.get("event_id") or event.get("create_time") or uuid.uuid4().hex
    session_key = f"{event['user_id']}:{event.get('chat_id') or ''}:{unique_key}"
    conversation_id = session_mapper.get_or_create("feishu", session_key)
    provider.send_message("已收到消息，正在处理中。", event)
    resp = daemon_client.send_message(conversation_id, event["text"], workspace_dir)
    if not resp or resp.get("status") != "ok":
        error_text = resp.get("error") if isinstance(resp, dict) else "Daemon offline"
        provider.send_message(f"⚠️ {error_text}", event)
        return None
    result = resp.get("result") or {}
    output = _extract_content(result)
    provider.send_message(output, event)
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
        _handle_im_event(payload, provider, session_mapper, config_manager, daemon_client)
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
