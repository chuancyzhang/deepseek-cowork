"""Optional enterprise access. Package discovery is local; credentials use DPAPI."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from functools import cached_property
import hashlib
import json
import logging
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
from urllib.parse import quote, urlsplit

import httpx

from core.enterprise_connection import read_connection, validate_connection, check_update_connection

log = logging.getLogger(__name__)
MODEL_PREFIX = "enterprise:"
_ANY_GENERATION = object()


def _same_platform(left, right):
    return (left["platform_id"] == right["platform_id"]
            and left["platform_url"].rstrip("/") == right["platform_url"].rstrip("/"))


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

    @cached_property
    def package_connection(self):
        try:
            return read_connection(), ""
        except (OSError, ValueError) as exc:
            log.warning("enterprise.package_connection_error reason=%s", type(exc).__name__)
            return None, str(exc) if isinstance(exc, ValueError) else "无法读取安装包的企业连接配置。"

    def _saved(self):
        if not self.path.exists():
            return {}, None
        with self.db() as db:
            row = db.execute("SELECT public, secret FROM connection WHERE id=1").fetchone()
        return (json.loads(row[0]), row[1]) if row else ({}, None)

    def connection_status(self):
        package, error = self.package_connection
        if error:
            return {"connection_source": "package", "connection_error": error}
        saved, _ = self._saved()
        if not package:
            return saved
        current = saved.get("connection")
        if current and not _same_platform(current, package):
            return {"connection": package, "connection_source": "package", "previous_connection": current}
        return {**saved, "connection": package, "connection_source": "package"}

    def read(self, *, secret=False):
        package, error = self.package_connection
        if error:
            return {}
        value, encrypted = self._saved()
        current = value.get("connection")
        if package and current and not _same_platform(current, package):
            return {}
        if secret and encrypted:
            try:
                value["access_token"] = self.crypt().unprotect(encrypted).decode("utf-8")
            except Exception:
                raise EnterpriseError("企业登录凭据无法读取，请重新登录。") from None
        return value

    def write(self, value, token=None, *, expected_generation=_ANY_GENERATION):
        value = dict(value)
        value.pop("access_token", None)
        encrypted = self.crypt().protect(token.encode("utf-8")) if token else None
        with self.db() as db:
            row = db.execute("SELECT public, secret FROM connection WHERE id=1").fetchone()
            current_generation = json.loads(row[0]).get("generation") if row else None
            if expected_generation is not _ANY_GENERATION and current_generation != expected_generation:
                raise EnterpriseError("企业连接已改变，请重新操作。")
            db.execute("INSERT OR REPLACE INTO connection VALUES(1,?,?)",
                       (json.dumps(value, ensure_ascii=False), encrypted))
            if row and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='chat_audit_pending'").fetchone():
                identity = ((value.get("connection") or {}).get("platform_id"), (value.get("member") or {}).get("id"),
                            value.get("generation"))
                if not token or not all(identity):
                    db.execute("DELETE FROM chat_audit_pending")
                else:
                    db.execute("DELETE FROM chat_audit_pending WHERE (platform_id,member_id,generation) != (?,?,?)", identity)

    def connection(self):
        status = self.connection_status()
        if status.get("connection_error"):
            raise EnterpriseError(status["connection_error"])
        if status.get("previous_connection"):
            raise EnterpriseError("请先在企业账号设置中确认切换到安装包提供的企业。")
        if not status.get("connection"):
            raise EnterpriseError("请先连接企业平台。")
        return status["connection"]

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
                response = client.request(method, connection["platform_url"].rstrip("/") + path, json=payload,
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

    def bind(self, connection=None, *, use_package=False, confirm_switch=False):
        package, error = self.store.package_connection
        if error:
            raise EnterpriseError(error)
        if use_package:
            if not package:
                raise EnterpriseError("未找到安装包提供的企业连接。")
            connection = package
        elif package:
            raise EnterpriseError("此安装包已指定企业平台。")
        connection = validate_connection(connection)
        old, _ = self.store._saved()
        current = old.get("connection")
        if current and _same_platform(current, connection):
            if use_package or current == connection:
                return False
            check_update_connection(current, connection)
        elif current and use_package and not confirm_switch:
            raise EnterpriseError("请先在企业账号设置中确认切换到安装包提供的企业。")
        self.inspect(connection)
        self.store.write({"connection": connection, "generation": uuid.uuid4().hex,
                          "client_id": old.get("client_id") or uuid.uuid4().hex},
                         expected_generation=old.get("generation"))
        return True

    def login(self, provider, cancel, open_browser, *, username="", password=""):
        connection = self.store.connection()
        if cancel.is_set():
            raise EnterpriseError("企业登录已取消。")
        state = self.store.read()
        verified = False
        if not state:
            verified = self.bind(use_package=True)
            state = self.store.read()
        if not state or not _same_platform(state["connection"], connection):
            raise EnterpriseError("企业连接已改变，请重新操作。")
        generation = state["generation"]
        if not verified:
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
            transaction_id = quote(transaction["id"], safe="")
            path = "/api/v1/auth/transactions/" + transaction_id
            completed = False
            try:
                login_url = transaction.get("login_url", "")
                browser_url = urlsplit(login_url)
                if (browser_url.scheme not in {"http", "https"} or not browser_url.hostname
                        or browser_url.username or browser_url.password or browser_url.query or browser_url.fragment
                        or not browser_url.path.endswith("/auth/start/" + transaction_id)):
                    raise EnterpriseError("企业平台返回的浏览器登录地址无效，请联系管理员检查登录配置。")
                open_browser(login_url)
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
        state.update(connection=connection, member=me["member"], models=me["models"], expires_at=result["expires_at"], generation=uuid.uuid4().hex,
                     chat_audit_version=me.get("chat_audit_version", 0))
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
        if not token:
            raise EnterpriseError("请先登录企业账号。")
        me = self.request(state["connection"], "GET", "/api/v1/me", token=token)
        state.update(member=me["member"], models=me["models"], chat_audit_version=me.get("chat_audit_version", 0))
        self.store.write(state, token, expected_generation=state["generation"])
        return state

    def logout(self):
        state = self.store.read(secret=True)
        if not state.get("connection"):
            raise EnterpriseError("当前没有可退出的企业账号。")
        token = state.get("access_token")
        # Invalidate local bindings immediately, even if the platform is unavailable.
        self.store.write({"connection": state["connection"], "client_id": state["client_id"], "generation": uuid.uuid4().hex},
                         expected_generation=state["generation"])
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
