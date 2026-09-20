"""Opt-in routes around existing providers; legacy credentials and references stay intact."""
from __future__ import annotations

import copy
import uuid

from .broker import ConnectionBroker, task_identity
from .errors import ConnectionError
from ..knowledge_library import KnowledgeError, KnowledgeService, same_identity


def knowledge_error(exc):
    return KnowledgeError(exc.code, str(exc), exc.status)


def projected(item, source, *, secret=False):
    who = item["identity"]
    if not who:
        raise ConnectionError("not_connected")
    public = {**item.get("profile", {}), "source": source, "base_url": item["template"]["base_url"],
              "connection_id": item["id"], "generation": item["generation"], "user_id": who["subject"],
              "tenant_id": who["tenant"], "email": item.get("profile", {}).get("label") or item["name"],
              "_connection_ref": item["id"]}
    # A legacy reference mapping is explicit and only created after verified identity equality.
    if item.get("legacy_reference_identity"):
        public["connection_id"] = item["legacy_reference_identity"]["connection_id"]
    if secret:
        public["credentials"] = item["credentials"]
    return public


class BoundStore:
    def __init__(self, legacy, broker, connection_id, source):
        self.legacy, self.broker, self.connection_id, self.source = legacy, broker, connection_id, source

    def __getattr__(self, name):
        return getattr(self.legacy, name)

    def connection(self, db=None, secret=False, source=None):
        try:
            item = self.broker.store.get(self.connection_id, secret=secret)
            if item["state"] == "disconnected":
                raise ConnectionError("revoked")
            return projected(item, self.source, secret=secret)
        except ConnectionError as exc:
            raise knowledge_error(exc) from None

    def save_connection(self, *args, **kwargs):
        raise KnowledgeError("managed_connection", "此来源使用统一连接，请到设置 → 账号与连接管理。")


class UnifiedMixin:
    def binding_for(self, scope, operation):
        from ..execution_authorization import current_authorization
        authorization = current_authorization()
        capability = "knowledge-library" if authorization else "library:" + self.source
        task = task_identity(authorization) if authorization else scope.get("_connection_task_id")
        resources = sorted({str(r.get("collection_id") or r.get("kb_id")) for r in scope.get("refs", []) if r.get("collection_id") or r.get("kb_id")})
        binding = self.broker.binding(task, capability, self.source, connection_id=self.connection_id,
                                      operations=["read", "write"] if operation == "write" else ["read"],
                                      resources=resources, service=self.source)
        return binding, resources, authorization

    def snapshot(self, refs=None, session_id=""):
        value = self.store.connection()
        return {**value, "refs": copy.deepcopy(refs or []), "session_id": session_id,
                "_connection_task_id": "library-ui:" + uuid.uuid4().hex}

    def logout(self):
        self.broker.disconnect(self.connection_id)

    def login(self, *args, **kwargs):
        raise KnowledgeError("managed_connection", "此来源使用统一连接，请到设置 → 账号与连接重新认证。")

    connect = login
    start_authorization = login
    finish_authorization = login
    switch_tenant = login

    def managed_call(self, scope, operation, call, cancelled=None, safe_retry=False):
        try:
            binding, resources, authorization = self.binding_for(scope, operation)
            def stopped():
                return bool((cancelled and cancelled()) or (authorization and authorization._cancelled.is_set()))
            def perform(_item):
                try:
                    return call()
                except KnowledgeError as exc:
                    if exc.status == 401:
                        raise ConnectionError("authentication_required", status=401) from None
                    if exc.status == 403:
                        raise ConnectionError("forbidden", status=403) from None
                    if exc.code == "outcome_unknown":
                        raise ConnectionError("outcome_unknown") from None
                    if operation == "write" and exc.code == "unavailable":
                        raise ConnectionError("outcome_unknown") from None
                    raise
            return self.broker.execute(binding, operation, perform, resources=resources, cancelled=stopped,
                                       safe_retry=safe_retry, wait_for_auth=authorization is not None)
        except ConnectionError as exc:
            raise knowledge_error(exc) from None


class UnifiedWeKnora(UnifiedMixin, KnowledgeService):
    source = "weknora"

    def switch_tenant(self, tenant_id):
        # Explicit UI action creates another binding target, never edits a running identity.
        original = self.broker.store.get(self.connection_id, secret=True)
        adapter = self.broker.adapters(original["template"])
        headers = adapter.headers(original["credentials"])
        headers["X-Tenant-ID"] = original["identity"]["tenant"]
        try:
            info = adapter.request("GET", original["template"]["base_url"] + "/api/v1/auth/me", headers=headers)
            info = info.get("data", info)
            if str(tenant_id) not in {str(x["tenant_id"]) for x in info.get("memberships", [])}:
                raise ConnectionError("forbidden")
            data = adapter.request("POST", original["template"]["base_url"] + "/api/v1/auth/switch-tenant", headers=headers,
                                   json={"tenant_id": int(tenant_id), "refresh_token": original["credentials"]["refresh_token"]})
            data = data.get("data", data)
            if (str((data.get("active_tenant") or {}).get("id")) != str(tenant_id)
                    or str((data.get("user") or {}).get("id")) != original["identity"]["subject"]
                    or not data.get("token") or not data.get("refresh_token")):
                raise ConnectionError("identity_changed")
            latest = self.broker.store.get(self.connection_id)
            if latest["revision"] != original["revision"]:
                raise ConnectionError("conflict")
            created = self.broker.store.create(original["template"], original["name"] + " · " + str(tenant_id))
            who = {**original["identity"], "tenant": str(tenant_id)}
            from .adapters import result
            saved = self.broker._save_outcome(created, result(who, {"token": data["token"], "refresh_token": data["refresh_token"]},
                                                           scopes=original["scopes"], profile=original.get("profile")))
            session_id = self.broker.store.save_session(saved)
            self.broker.store.update(saved["id"], {"login_session_id": session_id})
            usages = [(g["capability"], self.source, g["operations"], g["resources"])
                      for g in self.broker.store.grants(self.connection_id)
                      if g["capability"] in {"library:weknora", "knowledge-library"}
                      and self.broker.store.selection(g["capability"], self.source) == self.connection_id]
            self.broker.store.activate(saved["id"], usages)
            return saved
        except ConnectionError as exc:
            raise knowledge_error(exc) from None

    def request(self, scope, method, path, cancelled=None, **kwargs):
        readonly = method == "GET" or path == "/api/v1/knowledge-search"
        def perform():
            item = self.store.connection(secret=True)
            self._identity(scope, item)
            result = self._http(item["base_url"], method, path, token=item["credentials"]["token"],
                                tenant=scope["tenant_id"], cancelled=cancelled, **kwargs)
            self._identity(scope, self.store.connection())
            return result
        return self.managed_call(scope, "read" if readonly else "write", perform, cancelled, readonly)


def unified_provider(legacy, broker, connection_id, source):
    from ..knowledge_sources import TencentDocsProvider, LexiangProvider
    expected = {"weknora": "weknora", "tencent-docs": "tencent_docs", "lexiang": "lexiang"}[source]
    if broker.store.get(connection_id)["template"]["adapter"] != expected:
        raise ConnectionError("unsupported_adapter")
    if source == "weknora":
        cls = UnifiedWeKnora
    else:
        base = TencentDocsProvider if source == "tencent-docs" else LexiangProvider
        class UnifiedCloud(UnifiedMixin, base):
            def call(self, scope, tool, arguments, cancelled=None):
                readonly = tool not in self._write_tools
                return self.managed_call(scope, "read" if readonly else "write",
                    lambda: super(UnifiedCloud, self).call(scope, tool, arguments, cancelled), cancelled, readonly)
        cls = UnifiedCloud
    provider = cls(BoundStore(legacy.store, broker, connection_id, source), transport=legacy.transport)
    provider.broker, provider.connection_id = broker, connection_id
    return provider


class KnowledgeConnectionRouter:
    def __init__(self, legacy, source):
        self.legacy, self.source = legacy, source
        self.broker = ConnectionBroker(data_dir=legacy.store.data_dir)
        self._providers = {}

    def selected(self):
        return self.broker.store.selection("library:" + self.source, self.source)

    def _provider(self, scope=None):
        # Frozen legacy scopes must remain legacy after changing the default route.
        connection_id = scope.get("_connection_ref") if isinstance(scope, dict) else self.selected()
        if not connection_id:
            return self.legacy
        if connection_id not in self._providers:
            self._providers[connection_id] = unified_provider(self.legacy, self.broker, connection_id, self.source)
        return self._providers[connection_id]

    @property
    def store(self):
        return self._provider().store

    def snapshot(self, refs=None, session_id=""):
        try:
            return self._provider().snapshot(refs, session_id)
        except (ConnectionError, KnowledgeError):
            return {"source": self.source, "refs": refs or [], "session_id": session_id, "unavailable": True,
                    "_connection_ref": self.selected()}

    def __getattr__(self, name):
        attr = getattr(self.legacy, name)
        if not callable(attr):
            return getattr(self._provider(), name)
        def routed(*args, **kwargs):
            scope = args[0] if args and isinstance(args[0], dict) and ("generation" in args[0] or "unavailable" in args[0]) else None
            try:
                return getattr(self._provider(scope), name)(*args, **kwargs)
            except ConnectionError as exc:
                raise knowledge_error(exc) from None
        return routed


def adopt_references(broker, legacy_store, source, connection_id):
    """Explicit UI action. Never copies credentials or changes saved document selections."""
    old = legacy_store.connection(source=source)
    item = broker.store.get(connection_id)
    new = projected(item, source)
    if (not old or str(old.get("user_id")) != new["user_id"] or str(old.get("tenant_id", "")) != new["tenant_id"]
            or (source == "weknora" and old.get("base_url") != new["base_url"])):
        raise ConnectionError("identity_changed")
    broker.store.update(connection_id, {"legacy_reference_identity": {k: old[k] for k in ("connection_id", "user_id", "tenant_id")}},
                        expected_revision=item["revision"])
