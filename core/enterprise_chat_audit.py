"""Optional latest-request audit. The upload queue never replays a model request."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from urllib.parse import urlsplit

import httpx

from core.enterprise_client import EnterpriseStore


log = logging.getLogger(__name__)
RETENTION = 30 * 86400
MAX_BYTES = 4 * 1024 * 1024
MAX_PENDING = 64
RETRY_DELAYS = (3, 15, 60, 180)
_workers_lock = threading.Lock()
_workers = {}
_media_types = {"input_file", "output_file", "file", "image", "image_url", "input_image", "audio", "input_audio",
                "video", "input_video", "document", "base64", "computer_screenshot"}
_media_keys = {"file_data", "image_data", "audio_data", "video_data", "base64", "b64_json", "input_audio", "input_image",
               "input_video", "image_url", "file_url", "file_bytes", "file_content", "attachments"}
_secret_keys = {"authorization", "proxy_authorization", "cookie", "set_cookie", "headers", "extra_headers", "api_key",
                "x_api_key", "password", "passwd", "access_token", "refresh_token", "id_token", "token", "client_secret",
                "private_key", "secret_key", "credentials", "secret", "http_client", "_cowork"}
_data_url = re.compile(r"(?i)data:[^\s,;\"']{0,128}(?:;[^\s,;\"']{1,128})*,[^\s\"'<>`]+")
_secret_text = re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|token)"
                          r"\b[\"']?\s*[:=]\s*)(?:\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^\s,;&}\"']+)")
_bearer = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_inline_media = re.compile(r"(?<![A-Za-z0-9+/])(?:[A-Za-z0-9+/]{1024,}={0,2})(?![A-Za-z0-9+/])")


def sanitize_request(value, secrets=(), depth=0):
    if depth > 64:
        return "[嵌套内容已省略]"
    if isinstance(value, dict):
        media = value.get("type")
        if isinstance(media, str) and media in _media_types:
            return {"type": media, "content": "[文件或媒体内容已省略]"}
        sanitized = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            normalized = key.lower().replace("-", "_")
            if normalized not in _secret_keys:
                sanitized[key] = "[文件或媒体内容已省略]" if normalized in _media_keys else sanitize_request(item, secrets, depth + 1)
        return sanitized
    if isinstance(value, (tuple, list)):
        return [sanitize_request(item, secrets, depth + 1) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if isinstance(secret, str) and secret:
                if len(secret) >= 8 or value == secret:
                    value = value.replace(secret, "[已隐藏]")
        value = _data_url.sub("[媒体内容已省略]", value)
        value = _inline_media.sub("[疑似内嵌媒体内容已省略]", value)
        value = _bearer.sub("Bearer [已隐藏]", value)
        return _secret_text.sub(r"\1[已隐藏]", value)
    if isinstance(value, bytes):
        return "[二进制数据已省略]"
    return value


def scope(state):
    return ((state.get("connection") or {}).get("platform_id"), (state.get("member") or {}).get("id"),
            state.get("generation"))


def binding_scope(binding):
    return tuple(binding.get(key) for key in ("platform_id", "member_id", "generation"))


def contains_foreign_history(messages, identity):
    for message in messages:
        if not isinstance(message, dict):
            continue
        meta = message.get("meta")
        if not isinstance(meta, dict):
            continue
        submission = meta.get("enterprise_submission")
        markers = [meta.get("enterprise_context_identity"), submission.get("identity") if isinstance(submission, dict) else None]
        for marker in markers:
            if isinstance(marker, dict):
                owner = binding_scope(marker)[:2]
                if all(owner) and owner != identity[:2]:
                    return True
    return False


def request(state, method, path, payload=None):
    url = state["connection"]["platform_url"] + "/api/v1" + path
    loopback = urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"}
    with httpx.Client(timeout=5, follow_redirects=False, trust_env=not loopback) as client:
        return client.request(method, url, json=payload, headers={"Authorization": "Bearer " + state["access_token"]})


def queue_table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS chat_audit_pending (
        platform_id TEXT NOT NULL, member_id TEXT NOT NULL, generation TEXT NOT NULL, conversation_id TEXT NOT NULL,
        request_id TEXT NOT NULL, captured_us INTEGER NOT NULL, payload TEXT NOT NULL,
        tries INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (platform_id, member_id, generation, conversation_id))""")


def has_queue(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_audit_pending'").fetchone() is not None


def queue(store, identity, payload):
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    with store.db() as db:
        current = db.execute("SELECT public FROM connection WHERE id=1").fetchone()
        if not current or scope(json.loads(current[0])) != identity:
            return False
        queue_table(db)
        expired = int((time.time() - RETENTION) * 1_000_000)
        db.execute("DELETE FROM chat_audit_pending WHERE captured_us <= ?", (expired,))
        key = (*identity, payload["conversation_id"])
        exists = db.execute("SELECT 1 FROM chat_audit_pending WHERE (platform_id,member_id,generation,conversation_id) = (?,?,?,?)", key).fetchone()
        if not exists and db.execute("SELECT count(*) FROM chat_audit_pending").fetchone()[0] >= MAX_PENDING:
            raise RuntimeError("企业聊天审计待上传列表已满。")
        db.execute("""INSERT INTO chat_audit_pending
            (platform_id,member_id,generation,conversation_id,request_id,captured_us,payload) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT (platform_id,member_id,generation,conversation_id) DO UPDATE SET
            request_id=excluded.request_id,captured_us=excluded.captured_us,payload=excluded.payload,tries=0,retry_at=0
            WHERE (chat_audit_pending.captured_us,chat_audit_pending.request_id) < (excluded.captured_us,excluded.request_id)""",
                   (*key, payload["request_id"], payload["captured_us"], encoded))
    return True


def _drain_once(store):
    state = store.read(secret=True)
    identity = scope(state)
    now = time.time()
    with store.db() as db:
        if not has_queue(db):
            return None
        db.execute("DELETE FROM chat_audit_pending WHERE captured_us <= ?", (int((now - RETENTION) * 1_000_000),))
        if not all(identity) or not state.get("access_token") or state.get("expires_at", 0) <= now:
            return None
        row = db.execute("""SELECT conversation_id,request_id,captured_us,payload,tries,retry_at FROM chat_audit_pending
            WHERE (platform_id,member_id,generation)=(?,?,?) AND tries < 5 ORDER BY retry_at,captured_us LIMIT 1""", identity).fetchone()
    if row is None:
        return None
    conversation_id, request_id, captured_us, payload, tries, retry_at = row
    if retry_at > now:
        return retry_at - now
    # Bind again immediately before the network call. A concurrent sign out cannot substitute a new token.
    current = store.read(secret=True)
    if scope(current) != identity or not current.get("access_token") or current.get("expires_at", 0) <= time.time():
        return None
    status = 503
    try:
        status = request(current, "PUT", "/chat-audit/latest", json.loads(payload)).status_code
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("enterprise.chat_audit_transport_failed request=%s reason=%s", request_id, type(exc).__name__)
    key = (*identity, conversation_id, request_id, captured_us)
    selected = "(platform_id,member_id,generation,conversation_id,request_id,captured_us)=(?,?,?,?,?,?)"
    with store.db() as db:
        if 200 <= status < 300:
            db.execute("DELETE FROM chat_audit_pending WHERE " + selected, key)
            log.info("enterprise.chat_audit_uploaded request=%s member=%s", request_id, identity[1])
        else:
            retry = status in {408, 425, 429} or status >= 500
            tries = tries + 1 if retry else 5
            delay = RETRY_DELAYS[tries - 1] if tries <= len(RETRY_DELAYS) else 0
            db.execute("UPDATE chat_audit_pending SET tries=?,retry_at=? WHERE " + selected,
                       (tries, time.time() + delay, *key))
            log.warning("enterprise.chat_audit_upload_failed request=%s status=%s attempt=%s", request_id, status, tries)
    return 0


def pending_status(store, state):
    identity = scope(state)
    if not all(identity) or not store.path.exists():
        return {"pending": 0, "failed": 0}
    with store.db() as db:
        if not has_queue(db):
            return {"pending": 0, "failed": 0}
        pending, failed = db.execute("""SELECT count(CASE WHEN tries < 5 THEN 1 END),count(CASE WHEN tries >= 5 THEN 1 END)
            FROM chat_audit_pending WHERE (platform_id,member_id,generation)=(?,?,?) AND captured_us > ?""",
            (*identity, int((time.time() - RETENTION) * 1_000_000))).fetchone()
    return {"pending": pending, "failed": failed}


def start_sync(store):
    if not store.path.exists():
        return
    key = str(store.path.resolve())
    with _workers_lock:
        if key in _workers:
            _workers[key].set()
            return
        wake = _workers[key] = threading.Event()

    def sync():
        try:
            while True:
                wake.clear()
                wait = _drain_once(store)
                if wait is None:
                    with _workers_lock:
                        if wake.is_set():
                            continue
                        _workers.pop(key, None)
                        return
                wake.wait(wait)
        except Exception as exc:
            log.error("enterprise.chat_audit_sync_failed reason=%s", type(exc).__name__)
            with _workers_lock:
                _workers.pop(key, None)

    threading.Thread(target=sync, daemon=True, name="enterprise-chat-audit").start()


class ChatAuditCapture:
    def __init__(self, worker, provider, space_binding):
        self.worker = worker
        self.latest = None
        self.model_binding = getattr(provider, "enterprise_audit_binding", None) or {}
        self.store = None
        self.legacy_replaced = False
        self.permit = None
        self.secret = ""
        self.identity = None
        if worker.is_subagent or not worker.conversation_id or not worker.request_id:
            return
        capability_context = worker.run_context.get("enterprise_capabilities") or {}
        capability_binding = capability_context.get("binding") or {}
        binding = self.model_binding or capability_binding or space_binding
        if not isinstance(binding, dict) or not binding:
            return
        try:
            self._prepare(binding, capability_context, space_binding)
        except Exception as exc:
            log.warning("enterprise.chat_audit_scope_unavailable run=%s reason=%s", worker.request_id, type(exc).__name__)

    def _prepare(self, binding, capability_context, space_binding):
        store = EnterpriseStore(binding["data_dir"])
        state = store.read(secret=True)
        identity = scope(state)
        original = binding_scope(binding)
        expected_generation = original[2] or getattr(getattr(self.worker, "_team_space_client", None), "generation", None)
        if (identity != (*original[:2], expected_generation) or not all(identity)
                or not state.get("access_token") or state.get("expires_at", 0) <= time.time()):
            return
        if self.model_binding:
            supported = state.get("chat_audit_version") == 1
            if not supported:
                response = request(state, "GET", "/platform-info")
                supported = response.is_success and (response.json().get("chat_audit") or {}).get("version") == 1
            if not supported:
                return
            self.legacy_replaced = True
        if self._foreign_history(identity):
            message = "此聊天包含其他企业身份的历史，当前请求不进入企业审计。可新建聊天使用企业能力。"
            log.info("enterprise.chat_audit_skipped run=%s reason=foreign_enterprise_history", self.worker.request_id)
            self.worker.observability_signal.emit({"type": "enterprise_chat_audit", "status": "skipped",
                "reason": "foreign_enterprise_history", "message": message, "run_id": self.worker.request_id})
            self.worker.step_signal.emit(message)
            return
        if not self.model_binding:
            data = {"conversation_id": self.worker.conversation_id, "run_id": self.worker.request_id}
            if space_binding:
                data["space_id"] = space_binding["space_id"]
            else:
                data["capabilities"] = [{key: item[key] for key in ("id", "kind", "version")}
                                        for item in capability_context.get("items", [])]
            response = request(state, "POST", "/chat-audit/policy", data)
            if response.status_code == 404:
                return
            response.raise_for_status()
            self.permit = response.json().get("permit")
            if not self.permit:
                return
        self.secret = state["access_token"]
        self.identity, self.store = identity, store

    @property
    def enterprise_negotiated(self):
        return self.legacy_replaced

    def _foreign_history(self, identity):
        if contains_foreign_history(getattr(self.worker, "messages", ()), identity):
            return True
        storage = getattr(self.worker, "chat_storage", None)
        get_messages = getattr(storage, "get_messages", None)
        return callable(get_messages) and contains_foreign_history(get_messages(self.worker.conversation_id), identity)

    def capture(self, protocol, params, attempt_id):
        if self.store is None:
            return
        model = params.get("model")
        if not isinstance(model, str) or not model:
            return
        profile = self.worker.run_context.get("selected_model_profile") or {}
        secrets = (self.secret, getattr(self.worker, "api_key", ""), profile.get("api_key"))
        safe = sanitize_request(params, secrets)
        omitted = len(json.dumps(safe, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")) > MAX_BYTES
        captured_us = time.time_ns() // 1_000
        payload = {"conversation_id": self.worker.conversation_id, "run_id": self.worker.request_id,
                   "request_id": attempt_id, "captured_us": captured_us, "protocol": protocol,
                   "model_name": model if self.model_binding else sanitize_request(model, secrets),
                   "model_source": "enterprise" if self.model_binding else "personal",
                   "parameters": {} if omitted else safe, "omitted": omitted}
        if self.model_binding:
            payload["model_id"] = self.model_binding["model_id"]
        else:
            payload["permit"] = self.permit
        if queue(self.store, self.identity, payload):
            self.latest = attempt_id

    def finish(self):
        if self.store is not None and self.latest:
            start_sync(self.store)
