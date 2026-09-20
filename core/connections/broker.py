"""Host-owned identity, grants and bounded authentication recovery."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import time
import uuid
from urllib.parse import unquote, urlsplit

from .adapters import adapter_for, check_cancelled
from .errors import ConnectionError
from .store import ConnectionStore
from .templates import TemplateCatalog, safe_url, validate_template

log = logging.getLogger(__name__)


def redact(value, credentials):
    secrets = [str(v) for k, v in credentials.items() if isinstance(v, str) and v and k not in {"scope", "token_type"}]
    if isinstance(value, str):
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, "[已隐藏凭据]")
        return value
    if isinstance(value, dict):
        return {k: redact(v, credentials) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, credentials) for v in value]
    return value


def requirement_list(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConnectionError("invalid_template")
    seen, result = set(), []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) - {"id", "service", "auth", "operations", "resources", "delivery"}:
            raise ConnectionError("invalid_template")
        item = copy.deepcopy(raw)
        if (not isinstance(item.get("id"), str) or not item["id"] or item["id"] in seen
                or not isinstance(item.get("service"), str) or not item["service"]
                or not isinstance(item.get("auth", "optional"), str) or not isinstance(item.get("delivery", "host"), str)
                or item.get("auth", "optional") not in {"none", "optional", "required"}
                or item.get("delivery", "host") not in {"host", "mcp_headers"}
                or not isinstance(item.get("operations"), list) or not item["operations"]
                or not isinstance(item.get("resources", []), list)):
            raise ConnectionError("invalid_template")
        if any(not isinstance(s, str) or not s for s in item["operations"] + item.get("resources", [])):
            raise ConnectionError("invalid_template")
        seen.add(item["id"])
        result.append(item)
    return result


def task_identity(authorization):
    return str(authorization.session_id) + ":" + str(authorization.run_id)


class ConnectionBroker:
    def __init__(self, data_dir=None, base_dir=None, *, store=None, adapters=None, waiter=None):
        if data_dir is None or base_dir is None:
            from ..env_utils import get_app_data_dir, get_base_dir
            data_dir = data_dir or get_app_data_dir()
            base_dir = base_dir or get_base_dir()
        self.store = store or ConnectionStore(data_dir)
        self.catalog = TemplateCatalog(base_dir, data_dir)
        self.adapters = adapters or adapter_for
        self.waiter = waiter

    def create(self, template_id, name=""):
        return self.store.create(self.catalog.get(template_id), name)

    def authenticate(self, connection_id, inputs, *, cancelled=None, progress=None):
        with self.store.refresh_lock(connection_id, cancelled):
            return self._authenticate(connection_id, inputs, cancelled=cancelled, progress=progress)

    def _authenticate(self, connection_id, inputs, *, cancelled=None, progress=None):
        previous = self.store.get(connection_id)
        pending = self.store.update(connection_id, {"state": "authenticating"}, expected_revision=previous["revision"])
        log.info("connection.login.start connection=%s adapter=%s", connection_id, previous["template"]["adapter"])
        try:
            outcome = self.adapters(previous["template"]).login(inputs, cancelled=cancelled, progress=progress)
            check_cancelled(cancelled)
            self._check_identity(previous, outcome)
            item = self._save_outcome(pending, outcome)
            session_id = self.store.save_session(item)
            item = self.store.update(connection_id, {"login_session_id": session_id}, expected_revision=item["revision"])
            log.info("connection.login.finish connection=%s state=%s", connection_id, item["state"])
            return item
        except Exception as exc:
            code = exc.code if isinstance(exc, ConnectionError) else "invalid_response"
            log.warning("connection.login.error connection=%s code=%s", connection_id, code)
            try:
                self.store.update(connection_id, {"state": previous["state"], "last_error": code}, expected_revision=pending["revision"])
            except ConnectionError:
                pass  # A disconnect/cancel changed the record; never resurrect it.
            if isinstance(exc, ConnectionError):
                raise
            raise ConnectionError("invalid_response") from None

    @staticmethod
    def _check_identity(previous, outcome):
        if previous.get("identity") and (previous["identity"] != outcome["identity"] or previous["scopes"] != outcome["scopes"]):
            raise ConnectionError("identity_changed")

    def _save_outcome(self, previous, outcome):
        return self.store.update(previous["id"], {"identity": outcome["identity"], "scopes": outcome["scopes"],
            "profile": outcome.get("profile", previous.get("profile", {})), "last_error": None,
            "state": "ready" if outcome.get("verified") else "saved",
            "verified_at": time.time() if outcome.get("verified") else None},
            credentials=outcome["credentials"], expected_revision=previous["revision"])

    def verify(self, connection_id):
        with self.store.refresh_lock(connection_id):
            return self._verify(connection_id)

    def _verify(self, connection_id):
        item = self.store.get(connection_id, secret=True)
        if item["state"] == "disconnected":
            raise ConnectionError("revoked")
        log.info("connection.verify.start connection=%s", connection_id)
        try:
            outcome = self.adapters(item["template"]).verify(item)
            self._check_identity(item, outcome)
            result = self._save_outcome(item, outcome)
            log.info("connection.verify.finish connection=%s state=%s", connection_id, result["state"])
            return result
        except ConnectionError as exc:
            self.store.update(connection_id, {"last_error": exc.code, "state": exc.code if exc.code in {"authentication_required", "forbidden", "unavailable"} else item["state"]}, expected_revision=item["revision"])
            log.warning("connection.verify.error connection=%s code=%s", connection_id, exc.code)
            raise

    def select(self, capability, requirement, connection_id, *, service=None):
        if connection_id:
            item = self.store.get(connection_id)
            if service and item["template"]["service"] != service:
                raise ConnectionError("target_changed")
            self.verify(connection_id)
        self.store.select(capability, requirement, connection_id)
        log.info("connection.selection capability=%s requirement=%s mode=%s", capability, requirement, "connection" if connection_id else "legacy")

    def grant(self, connection_id, capability, operations, resources=()):
        value = self.store.grant(connection_id, capability, operations, resources)
        log.info("connection.grant connection=%s capability=%s", connection_id, capability)
        return value

    def revoke(self, connection_id, capability):
        self.store.revoke(connection_id, capability)
        log.info("connection.revoke connection=%s capability=%s", connection_id, capability)

    def disconnect(self, connection_id, *, remote=False):
        before = self.store.get(connection_id, secret=remote)
        self.store.disconnect(connection_id)
        log.info("connection.disconnect connection=%s", connection_id)
        if remote:
            try:
                return self.adapters(before["template"]).logout(before)
            except ConnectionError as exc:
                return {"remote": "failed", "code": exc.code, "local": "disconnected"}
        return {"local": "disconnected", "remote": "not_requested"}

    def binding(self, task_id, capability, requirement="default", *, connection_id=None, operations=(), resources=(), service=None):
        old = self.store.binding(task_id, capability, requirement)
        selected = connection_id or (old or {}).get("connection_id") or self.store.selection(capability, requirement)
        if not selected:
            raise ConnectionError("not_connected")
        item = self.store.get(selected)
        if service and item["template"]["service"] != service:
            raise ConnectionError("target_changed")
        try:
            return self.store.bind(task_id, capability, requirement, selected, operations, resources)
        except ConnectionError as exc:
            if exc.code != "grant_required":
                raise
            from ..execution_authorization import current_authorization
            authorization = current_authorization()
            if authorization is None or task_identity(authorization) != task_id:
                raise
            from ..interaction import interaction_service
            reply = interaction_service.create_request(authorization.session_id, "approval",
                f"允许能力 {capability} 使用连接“{item['name']}”？\n操作：{', '.join(operations)}\n资源范围：{', '.join(resources) or '该服务允许的范围'}\n此授权会记住，可在账号与连接中撤销。",
                title="允许使用此连接", source_tool="connection_authorization", timeout_seconds=86400,
                metadata={"run_id": authorization.run_id, "host_only": True},
                abort_check=authorization._cancelled.is_set, require_receiver=True)
            authorization.check()
            if reply.get("status") != "completed" or reply.get("approved") is not True:
                raise ConnectionError("grant_required")
            # Login/identity changes during approval invalidate the proposal.
            latest = self.store.get(selected)
            if latest["identity"] != item["identity"] or latest["generation"] != item["generation"]:
                raise ConnectionError("identity_changed")
            self.grant(selected, capability, operations, resources)
            return self.store.bind(task_id, capability, requirement, selected, operations, resources)

    def refresh(self, binding, observed_revision, cancelled=None):
        connection_id = binding["connection_id"]
        with self.store.refresh_lock(connection_id, cancelled):
            self.store.check_binding(binding)
            item = self.store.get(connection_id, secret=True)
            if item["revision"] != observed_revision and item["state"] == "ready":
                return item
            log.info("connection.refresh.start connection=%s task=%s", connection_id, binding["task_id"])
            outcome = self.adapters(item["template"]).refresh(item)
            check_cancelled(cancelled)
            self._check_identity(item, outcome)
            self.store.check_binding(binding)
            self._save_outcome(item, outcome)
            log.info("connection.refresh.finish connection=%s task=%s", connection_id, binding["task_id"])
            return self.store.get(connection_id, secret=True)

    @staticmethod
    def check_target(template, url):
        safe_url(url)
        targets = [template.get("base_url", ""), template.get("parameters", {}).get("mcp_url", "")]
        target = urlsplit(url)
        decoded = unquote(target.path)
        if any(part in {"..", "."} for part in decoded.split("/")) or "\\" in decoded:
            raise ConnectionError("target_changed")
        for base in targets:
            if not base:
                continue
            root = urlsplit(base)
            path = root.path.rstrip("/")
            if (target.scheme, target.hostname, target.port) == (root.scheme, root.hostname, root.port) and (decoded == path or decoded.startswith(path + "/")):
                return
        raise ConnectionError("target_changed")

    def execute(self, binding, operation, call, *, resources=(), cancelled=None, safe_retry=False, wait_for_auth=True):
        """call receives a temporary private record. Unknown writes are never replayed."""
        recovery_used = False
        while True:
            check_cancelled(cancelled)
            self.store.check_binding(binding, operation, resources)
            item = self.store.get(binding["connection_id"], secret=True)
            if item["state"] not in {"ready", "authentication_required", "forbidden", "unavailable"}:
                raise ConnectionError("not_connected")
            expires = item["credentials"].get("expires_at")
            needs_auth = item["state"] == "authentication_required" or not item["credentials"]
            expired = expires is not None and float(expires) <= time.time() + 30
            if needs_auth or expired:
                if recovery_used:
                    raise ConnectionError("authentication_required")
                recovery_used = True
                try:
                    self.refresh(binding, item["revision"], cancelled)
                except ConnectionError as exc:
                    if exc.code != "authentication_required":
                        raise
                    if not wait_for_auth:
                        raise
                    self.await_auth(binding, operation, cancelled)
                continue
            log.info("connection.call.start connection=%s task=%s operation=%s", item["id"], binding["task_id"], operation)
            try:
                result = call(item)
                check_cancelled(cancelled)
                self.store.check_binding(binding, operation, resources)
                log.info("connection.call.finish connection=%s task=%s operation=%s", item["id"], binding["task_id"], operation)
                return redact(result, item["credentials"])
            except ConnectionError as exc:
                log.warning("connection.call.error connection=%s task=%s code=%s", item["id"], binding["task_id"], exc.code)
                if exc.code != "authentication_required" or recovery_used:
                    raise
                # Authentication error on a write still does not prove it was not executed.
                if not safe_retry:
                    raise ConnectionError("outcome_unknown") from None
                recovery_used = True
                try:
                    self.refresh(binding, item["revision"], cancelled)
                except ConnectionError as refresh_error:
                    if refresh_error.code != "authentication_required" or not wait_for_auth:
                        raise
                    self.await_auth(binding, operation, cancelled)

    def await_auth(self, binding, operation, cancelled=None):
        wait = self.store.wait(binding, operation)
        current = self.store.get(binding["connection_id"])
        self.store.update(current["id"], {"state": "authentication_required"}, expected_revision=current["revision"])
        log.info("connection.wait.start connection=%s task=%s wait=%s", current["id"], binding["task_id"], wait["id"])
        try:
            if self.waiter:
                approved = self.waiter(binding, wait, cancelled)
            else:
                from ..execution_authorization import current_authorization
                from ..interaction import interaction_service
                authorization = current_authorization()
                if authorization is None or task_identity(authorization) != binding["task_id"]:
                    raise ConnectionError("authentication_required")
                response = interaction_service.create_request(authorization.session_id, "choice",
                    "请到设置 → 账号与连接，使用原账号重新认证，再选择继续。已完成的内容已保留。",
                    title="等待认证 · " + current["name"],
                    options=[{"label": "已完成认证，继续", "value": "continue"}, {"label": "取消本次操作", "value": "cancel"}],
                    timeout_seconds=86400, source_tool="connection_authentication", require_receiver=True,
                    metadata={"run_id": authorization.run_id, "connection_wait_id": wait["id"], "host_only": True},
                    abort_check=cancelled)
                approved = response.get("status") == "completed" and response.get("selected_options") == ["continue"]
            check_cancelled(cancelled)
            if not approved:
                self.store.resolve_wait(wait["id"], "cancelled")
                raise ConnectionError("cancelled")
            status = next((w["status"] for w in self.store.waits() if w["id"] == wait["id"]), "cancelled")
            if status != "waiting":
                raise ConnectionError("cancelled")
            self.store.check_binding(binding)
            if self.store.get(current["id"])["state"] != "ready":
                raise ConnectionError("authentication_required")
            self.store.resolve_wait(wait["id"], "ready")
            log.info("connection.wait.finish connection=%s task=%s", current["id"], binding["task_id"])
        except ConnectionError as exc:
            if exc.code == "cancelled":
                self.store.resolve_wait(wait["id"], "cancelled")
            else:
                self.store.resolve_wait(wait["id"], "interrupted")
            raise

    def request(self, binding, operation, method, url, *, resources=(), cancelled=None, **kwargs):
        if set(kwargs) - {"params", "json", "data", "headers", "files"}:
            raise ConnectionError("unsupported_operation")
        self.check_target(binding["template"], url)
        readonly = method.upper() in {"GET", "HEAD"}
        def perform(item):
            adapter = self.adapters(item["template"])
            headers = dict(kwargs.get("headers") or {})
            auth_headers = adapter.headers(item["credentials"])
            # User-supplied headers cannot override the connection's authentication.
            headers = {k: v for k, v in headers.items() if k.lower() not in {h.lower() for h in auth_headers}}
            headers.update(auth_headers)
            values = {k: v for k, v in kwargs.items() if k not in {"headers", "allow_redirects", "timeout"}}
            if item["template"]["adapter"] == "wecom_app":
                values["params"] = {**values.get("params", {}), "access_token": item["credentials"]["access_token"]}
            try:
                payload = adapter.request(method, url, headers=headers, **values)
            except ConnectionError as exc:
                if not readonly and exc.code == "unavailable":
                    raise ConnectionError("outcome_unknown") from None
                raise
            if item["template"]["adapter"] == "wecom_app" and payload.get("errcode", 0):
                if payload["errcode"] in {40014, 42001}:
                    raise ConnectionError("authentication_required")
                raise ConnectionError("forbidden")
            return payload
        return self.execute(binding, operation, perform, resources=resources, cancelled=cancelled, safe_retry=readonly)
