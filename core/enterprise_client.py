"""Optional enterprise access. Package discovery is local; credentials use DPAPI."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import logging
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
from urllib.parse import urlsplit

import httpx

from core.enterprise_connection import read_connection, validate_connection, check_update_connection

log = logging.getLogger(__name__)
MODEL_PREFIX = "enterprise:"


class EnterpriseError(RuntimeError):
    pass


class EnterpriseStore:
    def __init__(self, data_dir=None, protector=None):
        if data_dir is None:
            from core.env_utils import get_app_data_dir
            data_dir = get_app_data_dir()
        self.path = Path(data_dir) / "enterprise.sqlite3"
        self.protector = protector

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS connection (id INTEGER PRIMARY KEY, public TEXT NOT NULL, secret BLOB)")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def crypt(self):
        if self.protector is None:
            from core.variable_store import WindowsDpapiProtector
            self.protector = WindowsDpapiProtector()
        return self.protector

    def read(self, *, secret=False):
        if not self.path.exists():
            return {}
        with self.db() as db:
            row = db.execute("SELECT public, secret FROM connection WHERE id=1").fetchone()
        if not row:
            return {}
        value = json.loads(row[0])
        if secret and row[1]:
            try:
                value["access_token"] = self.crypt().unprotect(row[1]).decode("utf-8")
            except Exception:
                raise EnterpriseError("企业登录凭据无法读取，请重新登录。") from None
        return value

    def write(self, value, token=None, *, expected_generation=None):
        value = dict(value)
        value.pop("access_token", None)
        encrypted = self.crypt().protect(token.encode("utf-8")) if token else None
        with self.db() as db:
            row = db.execute("SELECT public, secret FROM connection WHERE id=1").fetchone()
            if expected_generation is not None and (not row or json.loads(row[0]).get("generation") != expected_generation):
                raise EnterpriseError("企业连接已改变，请重新操作。")
            db.execute("INSERT OR REPLACE INTO connection VALUES(1,?,?)",
                       (json.dumps(value, ensure_ascii=False), encrypted))

    def connection(self):
        return self.read().get("connection") or read_connection()

    def profiles(self):
        try:
            return self._profiles()
        except (sqlite3.DatabaseError, OSError, ValueError, TypeError, KeyError) as exc:
            # Optional enterprise state must not hide the user's personal models.
            # The connection panel reads the same state and presents the error.
            log.error("enterprise.profiles_unavailable reason=%s", type(exc).__name__)
            return []

    def _profiles(self):
        state = self.read()
        if not state.get("member") or state.get("expires_at", 0) <= time.time():
            return []
        connection = state["connection"]
        binding = {"platform_id": connection["platform_id"], "member_id": state["member"]["id"],
                   "generation": state["generation"], "data_dir": str(self.path.parent)}
        return [{**m.get("options", {}), "id": MODEL_PREFIX + connection["platform_id"] + ":" + m["id"],
                 "display_name": m["name"], "model_name": m["model_name"], "provider": m["provider"],
                 "provider_type": m["provider"], "channel_id": MODEL_PREFIX + connection["platform_id"],
                 "provider_display_name": connection["platform_name"], "channel_display_name": connection["platform_name"] + " · 企业",
                 "base_url": m["base_url"], "api_protocol": m["protocol"], "api_key": "enterprise-session",
                 "enterprise": {**binding, "model_id": m["id"], "model_revision": m.get("revision", 1)}} for m in state.get("models", [])]

    def bound_session(self, binding):
        state = self.read(secret=True)
        if (state.get("generation") != binding.get("generation")
                or (state.get("member") or {}).get("id") != binding.get("member_id")
                or (state.get("connection") or {}).get("platform_id") != binding.get("platform_id")
                or state.get("expires_at", 0) <= time.time() or not state.get("access_token")):
            raise EnterpriseError("本轮企业身份已失效或改变。输入已保留，请登录后重新发起本轮。")
        if binding.get("model_id") not in {m["id"] for m in state.get("models", [])}:
            raise EnterpriseError("此企业模型已不可用，请刷新企业连接并重新选择。")
        current = next(m for m in state["models"] if m["id"] == binding["model_id"])
        if current.get("revision", 1) != binding.get("model_revision", 1):
            raise EnterpriseError("企业模型配置已更新，请重新发起本轮，输入和已有输出仍保留。")
        return state


class EnterpriseClient:
    def __init__(self, store=None, *, transport=None):
        self.store = store or EnterpriseStore()
        self.transport = transport

    def request(self, connection, method, path, *, token="", payload=None):
        loopback = urlsplit(connection["platform_url"]).hostname in {"localhost", "127.0.0.1", "::1"}
        try:
            with httpx.Client(transport=self.transport, timeout=20, follow_redirects=False,
                              trust_env=not loopback) as client:
                response = client.request(method, connection["platform_url"] + path, json=payload,
                                          headers={"Authorization": "Bearer " + token} if token else {})
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("enterprise.request_failed method=%s loopback=%s reason=%s", method, loopback, type(exc).__name__)
            raise EnterpriseError("暂时无法连接企业平台，请检查网络后重试。") from None
        if not response.is_success:
            raise EnterpriseError(str((data.get("error") or {}).get("message") or "企业平台未完成请求，请重试。"))
        return data

    def inspect(self, connection):
        connection = validate_connection(connection)
        info = self.request(connection, "GET", "/api/v1/platform-info")
        if info.get("platform_id") != connection["platform_id"] or 1 not in info.get("supported_api_versions", []):
            raise EnterpriseError("企业标识或接口版本不匹配，未连接此平台。")
        return self.request(connection, "GET", "/api/v1/auth/providers")["items"]

    def bind(self, connection):
        self.inspect(connection)
        old = self.store.read()
        current = old.get("connection")
        if current == connection:
            return
        if current and all(current[k] == connection[k] for k in ("platform_id", "platform_url")):
            check_update_connection(current, connection)
        self.store.write({"connection": connection, "generation": uuid.uuid4().hex,
                          "client_id": old.get("client_id") or uuid.uuid4().hex})

    def login(self, provider, cancel, open_browser, *, username="", password=""):
        state = self.store.read()
        connection = state["connection"]
        generation = state["generation"]
        self.inspect(connection)
        if cancel.is_set():
            raise EnterpriseError("企业登录已取消。")
        if provider and provider["mode"] == "ldap":
            result = self.request(connection, "POST", "/api/v1/auth/ldap", payload={
                "mode": "desktop", "provider_id": provider["id"], "username": username, "password": password})
        else:
            verifier = secrets.token_urlsafe(48)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            transaction = self.request(connection, "POST", "/api/v1/auth/transactions", payload={
                "mode": "desktop", "provider_id": (provider or {}).get("id"), "code_challenge": challenge})
            path = "/api/v1/auth/transactions/" + transaction["id"]
            completed = False
            try:
                open_browser(transaction["login_url"])
                deadline = time.monotonic() + 600
                while not cancel.wait(1.5) and time.monotonic() < deadline:
                    result = self.request(connection, "POST", path + "/status", payload={"verifier": verifier})
                    if result.get("status") in {"failed", "cancelled", "consumed"}:
                        raise EnterpriseError("企业认证未完成，请重新登录。")
                    if result.get("handoff_code"):
                        result = self.request(connection, "POST", path + "/exchange", payload={
                            "verifier": verifier, "handoff_code": result["handoff_code"]})
                        completed = True
                        break
                if not completed:
                    raise EnterpriseError("企业登录已取消或超时，可重新登录。")
            finally:
                if not completed:
                    try:
                        self.request(connection, "POST", path + "/cancel", payload={"verifier": verifier})
                    except EnterpriseError:
                        log.warning("enterprise.login_cancel_remote_failed")
        token = result["access_token"]
        if cancel.is_set():
            self.request(connection, "POST", "/api/v1/auth/logout", token=token)
            raise EnterpriseError("企业登录已取消。")
        me = self.request(connection, "GET", "/api/v1/me", token=token)
        if me.get("platform_id") != connection["platform_id"]:
            raise EnterpriseError("企业身份校验失败。")
        state.update(member=me["member"], models=me["models"], expires_at=result["expires_at"], generation=uuid.uuid4().hex)
        try:
            self.store.write(state, token, expected_generation=generation)
        except Exception:
            self.request(connection, "POST", "/api/v1/auth/logout", token=token)
            raise
        log.info("enterprise.login_completed platform=%s member=%s", connection["platform_id"], me["member"]["id"])
        return state

    def refresh(self):
        state = self.store.read(secret=True)
        token = state.get("access_token", "")
        me = self.request(state["connection"], "GET", "/api/v1/me", token=token)
        state.update(member=me["member"], models=me["models"])
        self.store.write(state, token, expected_generation=state["generation"])
        return state

    def logout(self):
        state = self.store.read(secret=True)
        token = state.get("access_token")
        # Invalidate local bindings immediately, even if the platform is unavailable.
        self.store.write({"connection": state["connection"], "client_id": state["client_id"], "generation": uuid.uuid4().hex})
        if token:
            try:
                self.request(state["connection"], "POST", "/api/v1/auth/logout", token=token)
            except EnterpriseError:
                raise EnterpriseError("已退出本机；中心会话暂未注销，将在到期后失效。") from None


def submission(profile, text, *, previous=None):
    """Called only by the user submission boundary, never by history scanning."""
    binding = (profile or {}).get("enterprise")
    if not binding or not str(text).strip():
        return None
    identity = {k: binding[k] for k in ("platform_id", "member_id", "generation")}
    if (previous and all((previous.get("identity") or {}).get(k) == identity[k] for k in ("platform_id", "member_id"))
            and previous.get("text") == str(text)):
        return {**previous, "identity": identity}
    return {"id": uuid.uuid4().hex, "text": str(text), "sent_at": time.time(), "identity": identity}


def questions_for_binding(items, binding):
    identity = {k: binding.get(k) for k in ("platform_id", "member_id", "generation")}
    return [{k: q[k] for k in ("id", "text", "sent_at")} for q in items if q and q.get("identity") == identity]
