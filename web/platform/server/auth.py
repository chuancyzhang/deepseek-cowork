"""Server-side OAuth/OIDC and LDAP; desktop clients receive only Cowork sessions."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from urllib.parse import urlencode

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .config import service_url
from .database import AuthSource, Installation, LoginEntry, LoginSession, LoginTransaction, Member, Platform
from .database_accounts import DatabaseAccounts
from .security import PlatformError, challenge, digest, equal, token

log = logging.getLogger(__name__)
LOGIN_SECONDS = 600
ID_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"]
PUBLIC_CONFIG_FIELDS = {
    "issuer", "discovery_url", "authorize_url", "token_url", "userinfo_url", "jwks_url", "client_id",
    "scope", "pkce", "token_encoding", "client_auth", "include_redirect_uri", "userinfo_method",
    "userinfo_token_delivery", "userinfo_token_parameter", "userinfo_client_id", "access_token_path",
    "expires_in_path", "subject_token_path", "subject_userinfo_path", "name_path", "email_path",
    "error_field", "success_values", "authorize_parameters", "host", "port", "tls", "ca_file",
    "base_dn", "user_filter", "identity_attribute", "bind_dn",
}


def field(payload, path, default=None):
    if not path:
        return default
    value = payload
    for part in str(path).split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def validate_source(mode, config, secrets, settings):
    if mode not in {"oauth2", "oidc", "ldap", "database"} or not isinstance(config, dict):
        raise PlatformError("invalid_source", "请选择平台账号密码、OAuth2、OIDC 或 LDAP。")
    if mode == "database":
        if config or secrets:
            raise PlatformError("invalid_source", "平台账号密码无需外部连接参数，请在账号管理中创建员工账号。")
        return {}
    if set(config) - PUBLIC_CONFIG_FIELDS or set(secrets) - {"client_secret", "bind_password"}:
        raise PlatformError("invalid_source", "认证配置包含不支持的字段。")
    p = dict(config)
    if mode == "ldap":
        for key in ("host", "base_dn", "user_filter", "identity_attribute", "tls"):
            if not isinstance(p.get(key), str) or not p[key].strip():
                raise PlatformError("invalid_source", "LDAP 配置缺少字段：" + key)
        if p["tls"] not in {"ldaps", "starttls"} or "{username}" not in p["user_filter"]:
            raise PlatformError("invalid_source", "LDAP 必须启用证书校验，并提供含 {username} 的用户过滤条件。")
        if p.get("bind_dn") and not secrets.get("bind_password"):
            raise PlatformError("invalid_source", "请配置目录查询账号的受保护密码。")
        return p
    for key in ("issuer", "discovery_url", "authorize_url", "token_url", "userinfo_url", "jwks_url"):
        if p.get(key):
            service_url(p[key], allow_local_http=settings.development)
            # Issuers are exact identifiers, and some endpoint paths require '/'.
            p[key] = p[key].strip()
    if not p.get("client_id") or not isinstance(p["client_id"], str):
        raise PlatformError("invalid_source", "请填写 client_id。")
    if mode == "oidc" and not p.get("issuer"):
        raise PlatformError("invalid_source", "OIDC 必须配置 issuer。")
    if not p.get("discovery_url"):
        for key in ("authorize_url", "token_url"):
            if not p.get(key):
                raise PlatformError("invalid_source", "认证配置缺少端点：" + key)
        if mode == "oidc" and not p.get("jwks_url"):
            raise PlatformError("invalid_source", "OIDC 必须配置发现地址或 JWKS 端点。")
    if mode == "oauth2" and (not p.get("userinfo_url") or not (
            p.get("subject_token_path") or p.get("subject_userinfo_path"))):
        raise PlatformError("invalid_source", "OAuth2 必须提供用户信息端点和稳定用户标识映射。")
    defaults = {"pkce": "S256", "token_encoding": "form", "client_auth": "client_secret_basic",
                "include_redirect_uri": True, "userinfo_method": "GET", "userinfo_token_delivery": "bearer",
                "userinfo_token_parameter": "access_token", "userinfo_client_id": False,
                "access_token_path": "access_token", "expires_in_path": "expires_in",
                "scope": "openid profile email" if mode == "oidc" else "",
                "name_path": "name", "email_path": "email", "error_field": "error", "success_values": []}
    for key, value in defaults.items():
        p.setdefault(key, value)
    choices = {"pkce": {"S256", "disabled"}, "token_encoding": {"form", "json"},
               "client_auth": {"client_secret_basic", "client_secret_post", "none"},
               "userinfo_method": {"GET", "POST"}, "userinfo_token_delivery": {"bearer", "query", "form"}}
    for key, values in choices.items():
        if p[key] not in values:
            raise PlatformError("invalid_source", "认证选项无效：" + key)
    if p["client_auth"] != "none" and not secrets.get("client_secret"):
        raise PlatformError("invalid_source", "当前客户端认证方式需要 client_secret。")
    if p["userinfo_token_delivery"] == "form" and p["userinfo_method"] != "POST":
        raise PlatformError("invalid_source", "Form 令牌传递必须使用 POST。")
    extra = p.get("authorize_parameters", {})
    if not isinstance(extra, dict) or set(extra) & {
        "client_id", "redirect_uri", "response_type", "state", "nonce", "code_challenge", "code_challenge_method", "scope"
    } or any(not isinstance(k, str) or not isinstance(v, str) for k, v in extra.items()):
        raise PlatformError("invalid_source", "授权附加参数须为固定字符串，不能覆盖流程保护字段。")
    return p


def oauth_compatibility_preset(base_url):
    return {"authorize_url": base_url + "/idp/oauth2/authorize",
            "token_url": base_url + "/idp/oauth2/getToken", "userinfo_url": base_url + "/idp/oauth2/getUserInfo",
            "client_id": "", "pkce": "S256", "client_auth": "client_secret_post", "token_encoding": "form",
            "include_redirect_uri": False, "userinfo_method": "GET", "userinfo_token_delivery": "query",
            "userinfo_token_parameter": "access_token", "userinfo_client_id": True,
            "subject_token_path": "uid", "name_path": "displayName", "email_path": "mail",
            "error_field": "errcode", "success_values": []}


class Authentication:
    def __init__(self, settings, database, vault, client):
        self.settings, self.database, self.vault, self.client = settings, database, vault, client
        self.accounts = DatabaseAccounts(database)

    def public_sources(self):
        with self.database.transaction() as db:
            return [{"id": s.id, "name": s.name, "mode": s.mode, "verified": s.verified_revision == s.revision}
                    for s in db.scalars(select(AuthSource).where(AuthSource.enabled).order_by(AuthSource.name))]

    def save_source(self, payload, *, actor="", bootstrap=False):
        source_id = payload.get("id", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", source_id):
            raise PlatformError("invalid_source", "认证来源 ID 格式无效。")
        if not isinstance(payload.get("name"), str) or not 1 <= len(payload["name"].strip()) <= 80:
            raise PlatformError("invalid_source", "请填写 1–80 字的认证名称。")
        with self.database.transaction() as db:
            source = db.get(AuthSource, source_id)
            if source and payload.get("revision") != source.revision:
                raise PlatformError("conflict", "认证配置已被其他操作修改，请刷新核对。", 409)
            old_secrets = self.vault.open(source.secret) if source else {}
            secrets = dict(old_secrets)
            secrets.update(payload.get("secrets") or {})
            config = validate_source(payload.get("mode"), payload.get("config"), secrets, self.settings)
            if source and (source.mode != payload["mode"] or source.config.get("issuer") != config.get("issuer")):
                raise PlatformError("identity_source_changed", "更换身份来源需使用新的来源 ID。")
            if source is None:
                source = AuthSource(id=source_id, revision=1, verified_revision=0)
                db.add(source)
            else:
                changed = source.config != config or old_secrets != secrets or source.name != payload["name"]
                if changed:
                    source.revision += 1
            source.name, source.mode, source.config = payload["name"].strip(), payload["mode"], config
            source.secret = self.vault.seal(secrets)
            enabled = bool(payload.get("enabled"))
            if enabled and source.verified_revision != source.revision and not bootstrap:
                raise PlatformError("verification_required", "请先保存草稿并完成测试登录，再启用此配置。")
            source.enabled = enabled
            self.database.audit(db, "auth_source.saved", member_id=actor, target=source_id)
        return source_id

    async def json_request(self, method, url, *, config=None, **kwargs):
        try:
            # Never log URLs, payloads, response bodies or an exception containing a token.
            response = await self.client.request(method, url, follow_redirects=False, timeout=20, **kwargs)
            if response.status_code < 200 or response.status_code >= 300 or len(response.content) > 1024 * 1024:
                raise ValueError("invalid HTTP response")
            data = response.json()
            if not isinstance(data, dict) or data.get("error"):
                raise ValueError("invalid JSON response")
            error_key = (config or {}).get("error_field")
            missing = object()
            value = field(data, error_key, missing)
            if error_key and value is not missing and value not in (config or {}).get("success_values", []):
                raise ValueError("business error")
            return data
        except (httpx.HTTPError, ValueError, TypeError):
            raise PlatformError("auth_upstream_failed", "认证服务未返回有效结果，请检查连接或认证配置。", 502) from None

    async def resolved(self, snapshot):
        p = dict(snapshot["config"])
        if p.get("discovery_url"):
            metadata = await self.json_request("GET", p["discovery_url"])
            if snapshot["mode"] == "oidc" and metadata.get("issuer") != p["issuer"]:
                raise PlatformError("issuer_mismatch", "发现文档的身份来源与配置不一致。")
            for local, remote in {"authorize_url": "authorization_endpoint", "token_url": "token_endpoint",
                                  "userinfo_url": "userinfo_endpoint", "jwks_url": "jwks_uri"}.items():
                if not p.get(local) and metadata.get(remote):
                    service_url(metadata[remote], allow_local_http=self.settings.development)
                    p[local] = metadata[remote].strip()
        if not p.get("authorize_url") or not p.get("token_url") or (snapshot["mode"] == "oidc" and not p.get("jwks_url")):
            raise PlatformError("missing_endpoint", "认证发现文档缺少必要端点。")
        return p

    async def begin(self, source_id, mode, code_challenge="", *, test_actor=""):
        if mode not in {"web", "desktop"}:
            raise PlatformError("invalid_login", "登录方式无效。")
        if mode == "desktop" and not re.fullmatch(r"[A-Za-z0-9_-]{43}", code_challenge):
            raise PlatformError("invalid_login", "缺少有效的桌面交接校验。")
        with self.database.transaction() as db:
            if not source_id:
                entry = db.get(LoginEntry, 1)
                source_id = entry.provider_id if entry else self.settings.login_provider
                if not source_id:
                    enabled = list(db.scalars(select(AuthSource.id).where(AuthSource.enabled)))
                    if len(enabled) != 1:
                        raise PlatformError("login_entry_unconfigured", "企业登录入口尚未配置，请联系管理员指定统一登录方式。", 409)
                    source_id = enabled[0]
            source = db.get(AuthSource, source_id)
            if source is None or (not source.enabled and not test_actor):
                raise PlatformError("source_unavailable", "该认证来源当前不可用。", 404)
            snapshot = {"id": source.id, "name": source.name, "mode": source.mode,
                        "revision": source.revision, "verified_revision": source.verified_revision,
                        "config": source.config, "secrets": self.vault.open(source.secret), "test_actor": test_actor}
        if snapshot["mode"] not in {"ldap", "database"}:
            snapshot["config"] = await self.resolved(snapshot)
        state, verifier, nonce = token(), token(), token()
        tx = LoginTransaction(provider_id=source_id, source_snapshot=self.vault.seal(snapshot), mode=mode,
                              challenge=code_challenge, state_hash=digest(state), expires_at=time.time() + LOGIN_SECONDS,
                              transient_secret=self.vault.seal({"state": state, "verifier": verifier, "nonce": nonce}))
        with self.database.transaction() as db:
            db.add(tx)
            db.flush()
            self.database.audit(db, "login.created", target=tx.id)
        return {"id": tx.id, "login_url": f"{self.settings.public_url}/auth/start/{tx.id}", "expires_at": tx.expires_at}

    def launch(self, transaction_id):
        browser_token = token()
        with self.database.transaction() as db:
            tx = db.scalar(select(LoginTransaction).where(LoginTransaction.id == transaction_id).with_for_update())
            if tx is None or tx.status != "pending" or tx.expires_at <= time.time() or tx.browser_hash:
                raise PlatformError("login_expired", "此登录链接已使用或过期，请重新发起登录。", 409)
            tx.browser_hash = digest(browser_token)
            snapshot, transient = self.vault.open(tx.source_snapshot), self.vault.open(tx.transient_secret)
            if snapshot["mode"] in {"ldap", "database"}:
                return f"{self.settings.public_url}/auth/directory/{tx.id}", browser_token
            p = snapshot["config"]
            params = {**p.get("authorize_parameters", {}), "client_id": p["client_id"],
                      "redirect_uri": f"{self.settings.public_url}/auth/callback/{tx.provider_id}",
                      "response_type": "code", "state": transient["state"]}
            if p.get("scope"):
                params["scope"] = p["scope"]
            if p["pkce"] == "S256":
                params.update(code_challenge=challenge(transient["verifier"]), code_challenge_method="S256")
            if snapshot["mode"] == "oidc":
                params["nonce"] = transient["nonce"]
            return p["authorize_url"] + "?" + urlencode(params), browser_token

    def directory_transaction(self, transaction_id, cookies):
        with self.database.transaction() as db:
            tx = db.get(LoginTransaction, transaction_id)
            if not tx or tx.status != "pending" or tx.expires_at <= time.time():
                raise PlatformError("login_expired", "登录已结束或过期，请返回客户端重新登录。", 409)
            cookie = cookies.get("cowork_login_" + tx.id, "")
            if not cookie or not equal(tx.browser_hash, digest(cookie)):
                raise PlatformError("login_binding_failed", "请在发起登录的浏览器窗口继续。", 403)
            snapshot = self.vault.open(tx.source_snapshot)
            if snapshot["mode"] not in {"ldap", "database"}:
                raise PlatformError("source_unavailable", "该登录入口不可用。", 404)
            return {"provider_id": tx.provider_id, "name": snapshot["name"],
                    "state": self.vault.open(tx.transient_secret)["state"]}

    async def identity_from_code(self, snapshot, transient, code):
        p, secrets = snapshot["config"], snapshot["secrets"]
        data = {"grant_type": "authorization_code", "code": code}
        kwargs = {}
        if p["include_redirect_uri"]:
            data["redirect_uri"] = f"{self.settings.public_url}/auth/callback/{snapshot['id']}"
        if p["pkce"] == "S256":
            data["code_verifier"] = transient["verifier"]
        if p["client_auth"] == "client_secret_basic":
            kwargs["auth"] = httpx.BasicAuth(p["client_id"], secrets["client_secret"])
        else:
            data["client_id"] = p["client_id"]
            if p["client_auth"] == "client_secret_post":
                data["client_secret"] = secrets["client_secret"]
        kwargs["json" if p["token_encoding"] == "json" else "data"] = data
        tokens = await self.json_request("POST", p["token_url"], config=p, **kwargs)
        access_token = field(tokens, p["access_token_path"])
        try:
            lifetime = float(field(tokens, p["expires_in_path"]))
            if not isinstance(access_token, str) or not access_token or not 0 < lifetime < 10 ** 10:
                raise ValueError()
        except (TypeError, ValueError):
            raise PlatformError("invalid_token", "认证响应缺少有效访问令牌或到期时间。") from None
        claims = {}
        if snapshot["mode"] == "oidc":
            from authlib.jose import JsonWebToken
            from authlib.oidc.core import CodeIDToken
            jwks = await self.json_request("GET", p["jwks_url"])
            try:
                claims = JsonWebToken(ID_ALGORITHMS).decode(
                    tokens["id_token"], jwks, claims_cls=CodeIDToken,
                    claims_options={"iss": {"essential": True, "value": p["issuer"]},
                                    "aud": {"essential": True, "value": p["client_id"]},
                                    "exp": {"essential": True}, "sub": {"essential": True}},
                    claims_params={"nonce": transient["nonce"], "client_id": p["client_id"], "access_token": access_token})
                claims.validate(leeway=30)
                if not equal(str(claims.get("nonce", "")), transient["nonce"]):
                    raise ValueError()
            except Exception:
                raise PlatformError("invalid_identity", "身份令牌校验失败，请核对来源、签名和应用标识。") from None
        profile = dict(claims)
        if p.get("userinfo_url"):
            headers, user_data = {}, {}
            if p["userinfo_token_delivery"] == "bearer":
                headers["Authorization"] = "Bearer " + access_token
            else:
                user_data[p["userinfo_token_parameter"]] = access_token
            if p["userinfo_client_id"]:
                user_data["client_id"] = p["client_id"]
            kwargs = {"headers": headers}
            kwargs["data" if p["userinfo_token_delivery"] == "form" else "params"] = user_data
            profile = await self.json_request(p["userinfo_method"], p["userinfo_url"], config=p, **kwargs)
        if snapshot["mode"] == "oidc":
            subject = claims.get("sub")
            if not isinstance(subject, str):
                raise PlatformError("missing_subject", "身份令牌缺少有效的稳定用户标识。")
            if p.get("userinfo_url") and profile.get("sub") != subject:
                raise PlatformError("subject_mismatch", "用户资料与身份令牌不属于同一个成员。")
        else:
            token_subject = field(tokens, p.get("subject_token_path"))
            profile_subject = field(profile, p.get("subject_userinfo_path"))
            if p.get("subject_token_path") and token_subject is None:
                raise PlatformError("missing_subject", "令牌响应缺少稳定成员标识。")
            if p.get("subject_userinfo_path") and profile_subject is None:
                raise PlatformError("missing_subject", "用户资料缺少稳定成员标识。")
            if token_subject is not None and profile_subject is not None and str(token_subject) != str(profile_subject):
                raise PlatformError("subject_mismatch", "两个响应的成员标识不一致。")
            subject = token_subject if token_subject is not None else profile_subject
        if not isinstance(subject, (str, int)) or isinstance(subject, bool) or not 1 <= len(str(subject)) <= 512:
            raise PlatformError("missing_subject", "认证响应缺少稳定成员标识。")
        return {"subject": str(subject), "name": str(field(profile, p["name_path"], subject) or subject)[:256],
                "email": str(field(profile, p["email_path"], "") or "")[:512]}

    def register(self, snapshot, identity):
        bootstrap_candidate = (snapshot["id"] == self.settings.bootstrap_provider
                     and identity["subject"] == self.settings.bootstrap_subject)
        # Unique provider+subject plus retry resolves simultaneous first logins.
        for attempt in range(2):
            try:
                with self.database.transaction() as db:
                    db.scalar(select(Platform).where(Platform.id == 1).with_for_update())
                    installation = db.get(Installation, 1)
                    bootstrap = (bootstrap_candidate and not (installation and installation.completed)
                                 and not db.scalar(select(Member.id).limit(1)))
                    if snapshot.get("verified_revision") != snapshot["revision"] and not (bootstrap or snapshot.get("test_actor")):
                        raise PlatformError("source_not_verified", "管理员尚未完成此认证配置的测试登录。", 403)
                    source = db.get(AuthSource, snapshot["id"])
                    if source is None or source.revision != snapshot["revision"]:
                        raise PlatformError("source_changed", "登录期间认证配置已变更，请重新登录。", 409)
                    if not source.enabled and not snapshot.get("test_actor"):
                        raise PlatformError("source_disabled", "认证来源已停用。", 403)
                    if source.mode == "database":
                        account_member = db.scalar(select(Member).where(Member.provider_id == source.id,
                                                  Member.subject == identity["subject"]).with_for_update())
                        if not account_member or not account_member.enabled or account_member.revision != identity.get("_member_revision"):
                            raise PlatformError("account_changed", "登录期间账号已变更，请重新登录。", 409)
                    if snapshot.get("test_actor"):
                        actor = db.get(Member, snapshot["test_actor"])
                        if not actor or not actor.enabled or actor.role != "admin":
                            raise PlatformError("forbidden", "测试发起人的管理员权限已失效。", 403)
                        source.verified_revision = source.revision
                        self.database.audit(db, "auth_source.verified", member_id=actor.id, target=source.id)
                        return actor.id
                    member = db.scalar(select(Member).where(Member.provider_id == snapshot["id"], Member.subject == identity["subject"]))
                    if member is None:
                        member = Member(provider_id=snapshot["id"], subject=identity["subject"],
                                        name=identity["name"], email=identity["email"], role="admin" if bootstrap else "member")
                        db.add(member)
                        db.flush()
                        self.database.audit(db, "member.created", member_id=member.id, target=member.id)
                    if not member.enabled:
                        raise PlatformError("member_disabled", "此成员已停用，请联系管理员。", 403)
                    member.name, member.email, member.last_login_at = identity["name"], identity["email"], time.time()
                    if bootstrap:
                        source.verified_revision = source.revision
                        if installation:
                            installation.completed = True
                    return member.id
            except IntegrityError:
                if attempt:
                    raise

    def session(self, member_id, kind, *, expected_revision=None):
        session_token, csrf = token(), token()
        expires_at = time.time() + self.settings.session_seconds
        with self.database.transaction() as db:
            member = db.scalar(select(Member).where(Member.id == member_id).with_for_update())
            if not member or not member.enabled:
                raise PlatformError("member_disabled", "此成员当前不可用。", 403)
            if expected_revision is not None and member.revision != expected_revision:
                raise PlatformError("account_changed", "登录期间账号或密码已变更，请重新登录。", 409)
            db.add(LoginSession(token_hash=digest(session_token), member_id=member_id, kind=kind,
                                csrf_hash=digest(csrf), expires_at=expires_at))
            self.database.audit(db, "login.completed", member_id=member_id)
        return {"access_token": session_token, "csrf_token": csrf, "expires_at": expires_at}

    async def callback(self, source_id, state, code, cookies, upstream_error=False, credentials=None):
        with self.database.transaction() as db:
            tx = db.scalar(select(LoginTransaction).where(LoginTransaction.state_hash == digest(state)))
            if tx is None or tx.provider_id != source_id or tx.expires_at <= time.time():
                raise PlatformError("login_expired", "登录事务无效或已过期。", 400)
            cookie = cookies.get("cowork_login_" + tx.id, "")
            if not cookie or not equal(tx.browser_hash, digest(cookie)):
                raise PlatformError("login_binding_failed", "浏览器登录事务不匹配，请从原入口重新登录。", 403)
            claimed = db.execute(update(LoginTransaction).where(LoginTransaction.id == tx.id, LoginTransaction.status == "pending")
                                 .values(status="exchanging")).rowcount
            if not claimed:
                raise PlatformError("login_used", "此登录回调已使用或取消。", 409)
            snapshot, transient, mode, tx_id = self.vault.open(tx.source_snapshot), self.vault.open(tx.transient_secret), tx.mode, tx.id
        try:
            if upstream_error or (not code and credentials is None):
                raise PlatformError("login_rejected", "认证已取消或被认证服务拒绝。")
            if snapshot["mode"] == "ldap" and credentials is not None:
                identity = await self.ldap_identity(snapshot, *credentials)
            elif snapshot["mode"] == "database" and credentials is not None:
                identity = await asyncio.to_thread(self.accounts.authenticate, snapshot["id"], *credentials)
            elif snapshot["mode"] not in {"ldap", "database"} and credentials is None:
                identity = await self.identity_from_code(snapshot, transient, code)
            else:
                raise PlatformError("invalid_login", "登录方式不匹配。")
            member_id = self.register(snapshot, identity)
            handoff = token()
            with self.database.transaction() as db:
                tx = db.get(LoginTransaction, tx_id)
                if tx.status != "exchanging" or tx.expires_at <= time.time():
                    raise PlatformError("login_cancelled", "登录事务已取消或过期。", 409)
                tx.member_id, tx.status = member_id, "ready"
                tx.transient_secret = self.vault.seal({"handoff": handoff})
                tx.source_snapshot = self.vault.seal({"test_actor": snapshot.get("test_actor", ""),
                                                     "member_revision": identity.get("_member_revision")})
            if snapshot.get("test_actor"):
                return {"mode": "test", "transaction_id": tx_id}
            if mode == "web":
                with self.database.transaction() as db:
                    db.execute(update(LoginTransaction).where(LoginTransaction.id == tx_id).values(status="consumed"))
                return {"mode": "web", "transaction_id": tx_id,
                        **self.session(member_id, "web", expected_revision=identity.get("_member_revision"))}
            return {"mode": "desktop", "transaction_id": tx_id}
        except Exception as exc:
            reason = exc.code if isinstance(exc, PlatformError) else "auth_internal_failed"
            transient["attempts"] = transient.get("attempts", 0) + 1
            retry = snapshot["mode"] in {"ldap", "database"} and reason in {"ldap_failed", "invalid_credentials"} and transient["attempts"] < 5
            with self.database.transaction() as db:
                db.execute(update(LoginTransaction).where(LoginTransaction.id == tx_id, LoginTransaction.status == "exchanging")
                           .values(status="pending" if retry else "failed", error=reason,
                                   transient_secret=self.vault.seal(transient if retry else {})))
                self.database.audit(db, "login.failed", target=tx_id, outcome=reason)
            log.warning("login.failed transaction=%s reason=%s", tx_id, reason)
            if isinstance(exc, PlatformError):
                raise
            raise PlatformError("auth_failed", "认证未完成，请重试或联系管理员检查认证配置。", 502) from None

    def desktop_status(self, transaction_id, verifier, *, cancel=False, handoff=None):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", verifier or ""):
            raise PlatformError("invalid_proof", "桌面交接校验无效。", 403)
        with self.database.transaction() as db:
            tx = db.scalar(select(LoginTransaction).where(LoginTransaction.id == transaction_id).with_for_update())
            if tx is None or tx.mode != "desktop" or not equal(tx.challenge, challenge(verifier)):
                raise PlatformError("invalid_proof", "桌面交接校验无效。", 403)
            if tx.expires_at <= time.time():
                raise PlatformError("login_expired", "登录已过期，请重新发起。", 410)
            if cancel:
                if tx.status != "consumed":
                    tx.status, tx.transient_secret = "cancelled", self.vault.seal({})
                return {"status": tx.status}
            if handoff is None:
                result = {"status": tx.status, "error": tx.error}
                if tx.status == "ready":
                    result["handoff_code"] = self.vault.open(tx.transient_secret)["handoff"]
                return result
            if tx.status != "ready" or not equal(self.vault.open(tx.transient_secret).get("handoff"), handoff):
                raise PlatformError("handoff_used", "交接码无效、已使用或已取消，请重新登录。", 409)
            tx.status, tx.transient_secret = "consumed", self.vault.seal({})
            member_id = tx.member_id
            member_revision = self.vault.open(tx.source_snapshot).get("member_revision")
        return {"status": "completed", **self.session(member_id, "desktop", expected_revision=member_revision)}

    async def ldap_login(self, source_id, username, password, *, kind="web", test_actor=""):
        with self.database.transaction() as db:
            source = db.get(AuthSource, source_id)
            if not source or source.mode != "ldap" or (not source.enabled and not test_actor):
                raise PlatformError("source_unavailable", "LDAP 来源不可用。", 404)
            snapshot = {"id": source.id, "mode": "ldap", "revision": source.revision,
                        "verified_revision": source.verified_revision, "config": source.config,
                        "secrets": self.vault.open(source.secret), "test_actor": test_actor}
        identity = await self.ldap_identity(snapshot, username, password)
        member_id = self.register(snapshot, identity)
        if test_actor:
            return {"verified": True}
        return self.session(member_id, kind)

    async def ldap_identity(self, snapshot, username, password):
        p = snapshot["config"]
        if not username or not password or len(username) > 512 or len(password) > 4096:
            raise PlatformError("invalid_credentials", "请填写有效的目录账号和密码。")

        def authenticate():
            import ssl
            import ldap3
            from ldap3.utils.conv import escape_filter_chars
            tls = ldap3.Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=p.get("ca_file"))
            server = ldap3.Server(p["host"], port=int(p.get("port", 636 if p["tls"] == "ldaps" else 389)),
                                 use_ssl=p["tls"] == "ldaps", tls=tls, connect_timeout=10)
            bind_mode = ldap3.AUTO_BIND_TLS_BEFORE_BIND if p["tls"] == "starttls" else ldap3.AUTO_BIND_NO_TLS
            with ldap3.Connection(server, user=p.get("bind_dn") or username,
                                  password=snapshot["secrets"].get("bind_password") if p.get("bind_dn") else password,
                                  auto_bind=bind_mode, receive_timeout=20, raise_exceptions=True) as search:
                attribute = p["identity_attribute"]
                search.search(p["base_dn"], p["user_filter"].replace("{username}", escape_filter_chars(username)), attributes=[attribute])
                if len(search.entries) != 1 or not search.entries[0][attribute].value:
                    raise ValueError("stable identity missing")
                entry = search.entries[0]
                subject, dn = str(entry[attribute].value), entry.entry_dn
            with ldap3.Connection(server, user=dn, password=password, auto_bind=bind_mode, receive_timeout=20, raise_exceptions=True):
                pass
            return {"subject": subject, "name": username, "email": ""}
        try:
            identity = await asyncio.to_thread(authenticate)
        except Exception:
            log.warning("ldap.failed provider=%s", snapshot["id"])
            raise PlatformError("ldap_failed", "目录认证失败，请检查账号、密码、证书或目录连接。", 401) from None
        return identity
