"""Lazily imported authentication adapters. Only the host sees credential material."""
from __future__ import annotations

import copy
import hashlib
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from .errors import ConnectionError
from .templates import safe_url


def check_cancelled(cancelled):
    if cancelled and cancelled():
        raise ConnectionError("cancelled")


def identity(issuer, subject, tenant="", kind="person"):
    if not subject:
        raise ConnectionError("invalid_response")
    return {"issuer": str(issuer), "subject": str(subject), "tenant": str(tenant), "kind": kind}


def result(who, credentials, *, scopes=(), profile=None, verified=True):
    return {"identity": who, "credentials": credentials, "scopes": sorted(set(scopes)),
            "profile": profile or {}, "verified": verified}


class Adapter:
    def __init__(self, template, transport=None):
        self.template = template
        self.params = template.get("parameters", {})
        self.base = template.get("base_url", "")
        if transport is None:
            import requests
            transport = requests
        self.transport = transport

    def request(self, method, url, **kwargs):
        safe_url(url)
        try:
            response = self.transport.request(method, url, timeout=(5, 30), allow_redirects=False, **kwargs)
        except Exception:
            raise ConnectionError("unavailable") from None
        if response.status_code == 401:
            raise ConnectionError("authentication_required", status=401)
        if response.status_code == 403:
            raise ConnectionError("forbidden", status=403)
        if not 200 <= response.status_code < 300:
            raise ConnectionError("unavailable" if response.status_code >= 500 else "invalid_response", status=response.status_code)
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except ValueError:
            raise ConnectionError("invalid_response") from None

    def login(self, inputs, *, cancelled=None, progress=None):
        raise ConnectionError("unsupported_operation")

    def verify(self, connection):
        return result(connection["identity"], connection["credentials"], scopes=connection["scopes"],
                      profile=connection.get("profile"), verified=False)

    def refresh(self, connection):
        raise ConnectionError("authentication_required")

    def headers(self, credentials):
        token = credentials.get("access_token") or credentials.get("token")
        if not token or any(c in str(token) for c in "\r\n"):
            raise ConnectionError("authentication_required")
        return {"Authorization": "Bearer " + token}

    def logout(self, connection):
        return {"remote": "unsupported"}


class ApiKeyAdapter(Adapter):
    def headers(self, credentials):
        token = credentials.get("token", "")
        if not token or any(c in token for c in "\r\n"):
            raise ConnectionError("authentication_required")
        return {self.params.get("header", "Authorization"): self.params.get("prefix", "Bearer ") + token}

    def login(self, inputs, *, cancelled=None, progress=None):
        check_cancelled(cancelled)
        credentials = {"token": inputs.get("token", "").strip()}
        headers = self.headers(credentials)
        verify_url = self.params.get("verify_url")
        data = self.request("GET", verify_url, headers=headers) if verify_url else {}
        # Without a service identity endpoint, a replacement key is a new identity.
        subject = data.get(self.params.get("identity_field", "")) or hashlib.sha256(credentials["token"].encode()).hexdigest()
        tenant = data.get(self.params.get("tenant_field", ""), "")
        return result(identity(self.base, subject, tenant, "credential" if not data else "person"),
                      credentials, verified=bool(verify_url))

    def verify(self, connection):
        return self.login(connection["credentials"])


class SupersetAdapter(Adapter):
    def _identity(self, token):
        data = self.request("GET", self.base + "/api/v1/me/", headers={"Authorization": "Bearer " + token})
        user = data.get("result") or {}
        return identity(self.base, user.get("id")), {"label": str(user.get("username", ""))}

    def login(self, inputs, *, cancelled=None, progress=None):
        check_cancelled(cancelled)
        data = self.request("POST", self.base + "/api/v1/security/login", json={
            "username": inputs.get("username", ""), "password": inputs.get("password", ""),
            "provider": self.params.get("provider", "db"), "refresh": True})
        if not data.get("access_token") or not data.get("refresh_token"):
            raise ConnectionError("invalid_response")
        who, profile = self._identity(data["access_token"])
        return result(who, {k: data[k] for k in ("access_token", "refresh_token")}, profile=profile)

    def verify(self, connection):
        who, profile = self._identity(connection["credentials"]["access_token"])
        return result(who, connection["credentials"], profile=profile)

    def refresh(self, connection):
        old = connection["credentials"]
        data = self.request("POST", self.base + "/api/v1/security/refresh",
                            headers={"Authorization": "Bearer " + old.get("refresh_token", "")})
        if not data.get("access_token"):
            raise ConnectionError("invalid_response")
        updated = {"access_token": data["access_token"], "refresh_token": data.get("refresh_token", old["refresh_token"])}
        who, profile = self._identity(updated["access_token"])
        return result(who, updated, profile=profile)


class WeKnoraAdapter(Adapter):
    def login(self, inputs, *, cancelled=None, progress=None):
        check_cancelled(cancelled)
        data = self.request("POST", self.base + "/api/v1/auth/login",
                            json={"email": inputs.get("username", ""), "password": inputs.get("password", "")})
        data = data.get("data", data)
        user = data.get("user") or {}
        tenant = str((data.get("tenant") or data.get("active_tenant") or {}).get("id") or "")
        if not data.get("token") or not data.get("refresh_token"):
            raise ConnectionError("invalid_response")
        return result(identity(self.base, user.get("id"), tenant),
                      {"token": data["token"], "refresh_token": data["refresh_token"]},
                      profile={"label": str(user.get("email", inputs.get("username", "")))})

    def verify(self, connection):
        data = self.request("GET", self.base + "/api/v1/auth/me", headers={**self.headers(connection["credentials"]),
                            "X-Tenant-ID": connection["identity"]["tenant"]})
        data = data.get("data", data)
        user = data.get("user") or data
        if str(user.get("id", "")) != connection["identity"]["subject"]:
            raise ConnectionError("identity_changed")
        active = data.get("active_tenant") or data.get("tenant")
        if active and str(active.get("id", "")) != connection["identity"]["tenant"]:
            raise ConnectionError("identity_changed")
        return result(connection["identity"], connection["credentials"], profile=connection.get("profile"))

    def refresh(self, connection):
        data = self.request("POST", self.base + "/api/v1/auth/refresh",
                            json={"refreshToken": connection["credentials"].get("refresh_token", "")})
        data = data.get("data", data)
        if not data.get("access_token") or not data.get("refresh_token"):
            raise ConnectionError("invalid_response")
        updated = {**connection, "credentials": {"token": data["access_token"], "refresh_token": data["refresh_token"]}}
        return self.verify(updated)

    def logout(self, connection):
        self.request("POST", self.base + "/api/v1/auth/logout", headers=self.headers(connection["credentials"]))
        return {"remote": "revoked"}


class OAuthAdapter(Adapter):
    def metadata(self):
        params = dict(self.params)
        params.setdefault("scope", "" if params.get("flow") == "client_credentials" else "openid profile")
        params.setdefault("oidc", "openid" in params["scope"].split())
        issuer = params.get("issuer", "")
        if params.get("discovery_url") or (issuer and not params.get("token_endpoint")):
            discovered = self.request("GET", params.get("discovery_url") or issuer.rstrip("/") + "/.well-known/openid-configuration")
            if issuer and discovered.get("issuer") != issuer:
                raise ConnectionError("invalid_response")
            allowed = {"issuer", "authorization_endpoint", "token_endpoint", "jwks_uri", "userinfo_endpoint",
                       "revocation_endpoint", "device_authorization_endpoint"}
            params = {**{k: v for k, v in discovered.items() if k in allowed}, **params}
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "userinfo_endpoint", "revocation_endpoint", "device_authorization_endpoint"):
            if params.get(key):
                safe_url(params[key])
        if not params.get("token_endpoint"):
            raise ConnectionError("invalid_template")
        return params

    def client(self, params, *, token=None, client_secret=None):
        try:
            from authlib.integrations.requests_client import OAuth2Session
        except ImportError:
            raise ConnectionError("dependency_missing") from None
        client = OAuth2Session(params["client_id"], client_secret=client_secret,
                               token_endpoint_auth_method="client_secret_post" if client_secret else "none",
                               scope=params.get("scope", "openid profile"), token=token, code_challenge_method="S256")
        return client

    def _who(self, params, token, nonce=None, previous=None):
        if params.get("flow") == "client_credentials":
            return identity(params.get("issuer") or params["token_endpoint"], params["client_id"], params.get("audience", ""), "application")
        oidc = params.get("oidc", "openid" in params.get("scope", "openid profile").split())
        claims = {}
        if token.get("id_token"):
            try:
                from authlib.jose import JsonWebToken
                if not params.get("jwks_uri") or not params.get("issuer"):
                    raise ConnectionError("invalid_template")
                keys = self.request("GET", params["jwks_uri"])
                options = {"iss": {"essential": True, "value": params["issuer"]},
                           "aud": {"essential": True, "value": params["client_id"]},
                           "exp": {"essential": True}, "sub": {"essential": True}}
                if nonce:
                    options["nonce"] = {"essential": True, "value": nonce}
                claims = JsonWebToken(["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA"]).decode(token["id_token"], keys, claims_options=options)
                claims.validate(leeway=30)
                if claims.get("azp") and claims["azp"] != params["client_id"]:
                    raise ValueError()
                if isinstance(claims.get("aud"), list) and len(claims["aud"]) > 1 and claims.get("azp") != params["client_id"]:
                    raise ValueError()
            except ImportError:
                raise ConnectionError("dependency_missing") from None
            except Exception:
                raise ConnectionError("invalid_response") from None
        elif oidc and not previous:
            raise ConnectionError("invalid_response")
        if params.get("userinfo_endpoint"):
            user = self.request("GET", params["userinfo_endpoint"], headers={"Authorization": "Bearer " + token["access_token"]})
            if claims and user.get("sub") != claims.get("sub"):
                raise ConnectionError("identity_changed")
            claims = {**claims, **user}
        if claims.get("sub"):
            return identity(params.get("issuer") or params["token_endpoint"], claims["sub"],
                            claims.get(params.get("tenant_claim", "tid"), ""))
        if previous:
            return previous
        return identity(params.get("issuer") or params["token_endpoint"], hashlib.sha256(token["access_token"].encode()).hexdigest(), kind="application" if params.get("flow") == "client_credentials" else "credential")

    def _result(self, params, token, *, nonce=None, previous=None):
        if not isinstance(token, dict) or not token.get("access_token") or str(token.get("token_type", "Bearer")).lower() != "bearer":
            raise ConnectionError("invalid_response")
        who = self._who(params, token, nonce, previous["identity"] if previous else None)
        scopes = str(token.get("scope", " ".join(previous["scopes"]) if previous else params.get("scope", "openid profile"))).split()
        credentials = {k: token[k] for k in ("access_token", "refresh_token", "expires_at", "expires_in", "token_type", "scope", "id_token") if k in token}
        if "expires_at" not in credentials and credentials.get("expires_in"):
            credentials["expires_at"] = time.time() + float(credentials["expires_in"])
        if previous and params.get("flow") == "client_credentials" and previous["credentials"].get("client_secret"):
            credentials["client_secret"] = previous["credentials"]["client_secret"]
        return result(who, credentials, scopes=scopes)

    def login(self, inputs, *, cancelled=None, progress=None):
        params = self.metadata()
        flow = params.get("flow", "authorization_code")
        if flow == "device_code":
            return self.device_login(params, cancelled, progress)
        with self.client(params, client_secret=inputs.get("client_secret") if flow == "client_credentials" else None) as client:
            try:
                if flow == "client_credentials":
                    check_cancelled(cancelled)
                    token = client.fetch_token(params["token_endpoint"], grant_type="client_credentials",
                                               timeout=30, allow_redirects=False,
                                               **({"audience": params["audience"]} if params.get("audience") else {}))
                    output = self._result(params, token)
                    # Application secret stays in the protected credential record only.
                    if inputs.get("client_secret"):
                        output["credentials"]["client_secret"] = inputs["client_secret"]
                    return output
                if not params.get("authorization_endpoint"):
                    raise ConnectionError("invalid_template")
                state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
                received = {}

                class Callback(BaseHTTPRequestHandler):
                    def do_GET(self):
                        query = parse_qs(urlsplit(self.path).query)
                        valid = urlsplit(self.path).path == "/callback" and query.get("state") == [state]
                        if valid and not received:
                            received.update(query)
                        self.send_response(200 if valid else 400)
                        self.send_header("Content-Type", "text/plain; charset=utf-8")
                        self.end_headers()
                        self.wfile.write("请返回 Cowork 完成连接。".encode("utf-8"))

                    def log_message(self, *_args):
                        pass  # Authorization codes must never be logged.

                with HTTPServer(("127.0.0.1", params.get("redirect_port", 0)), Callback) as server:
                    server.timeout = 0.2
                    client.redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"
                    url, _ = client.create_authorization_url(params["authorization_endpoint"], state=state,
                                                            nonce=nonce, code_verifier=verifier, code_challenge_method="S256",
                                                            **({"audience": params["audience"]} if params.get("audience") else {}))
                    if progress:
                        progress({"stage": "browser", "url": url})
                    else:
                        webbrowser.open(url)
                    deadline = time.monotonic() + 300
                    while not received and time.monotonic() < deadline:
                        check_cancelled(cancelled)
                        server.handle_request()
                    check_cancelled(cancelled)
                    if not received.get("code") or received.get("error"):
                        raise ConnectionError("authentication_required")
                    token = client.fetch_token(params["token_endpoint"], code=received["code"][0], code_verifier=verifier,
                                               timeout=30, allow_redirects=False)
                    return self._result(params, token, nonce=nonce)
            except ConnectionError:
                raise
            except Exception:
                raise ConnectionError("authentication_required") from None

    def device_login(self, params, cancelled, progress):
        endpoint = params.get("device_authorization_endpoint")
        if not endpoint:
            raise ConnectionError("unsupported_operation")
        device = self.request("POST", endpoint, data={"client_id": params["client_id"], "scope": params.get("scope", "openid profile")})
        if not all(device.get(k) for k in ("device_code", "user_code", "verification_uri", "expires_in")):
            raise ConnectionError("invalid_response")
        uri = safe_url(device.get("verification_uri_complete") or device["verification_uri"])
        if progress:
            progress({"stage": "device", "url": uri, "user_code": str(device["user_code"])})
        interval, deadline = max(1, int(device.get("interval", 5))), time.monotonic() + min(900, int(device["expires_in"]))
        while time.monotonic() < deadline:
            end = time.monotonic() + interval
            while time.monotonic() < end:
                check_cancelled(cancelled)
                time.sleep(0.1)
            try:
                response = self.transport.request("POST", params["token_endpoint"], data={"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device["device_code"], "client_id": params["client_id"]}, timeout=(5, 30), allow_redirects=False)
                token = response.json()
            except Exception:
                raise ConnectionError("unavailable") from None
            if token.get("error") == "authorization_pending":
                continue
            if token.get("error") == "slow_down":
                interval += 5
                continue
            if response.status_code != 200 or token.get("error"):
                raise ConnectionError("authentication_required")
            return self._result(params, token)
        raise ConnectionError("authentication_required")

    def verify(self, connection):
        return self._result(self.metadata(), connection["credentials"], previous=connection)

    def refresh(self, connection):
        params = self.metadata()
        if params.get("flow") == "client_credentials":
            return self.login(connection["credentials"])
        if not connection["credentials"].get("refresh_token"):
            raise ConnectionError("authentication_required")
        with self.client(params, token=connection["credentials"]) as client:
            try:
                token = client.refresh_token(params["token_endpoint"], refresh_token=connection["credentials"]["refresh_token"], timeout=30, allow_redirects=False)
                token.setdefault("refresh_token", connection["credentials"]["refresh_token"])
                return self._result(params, token, previous=connection)
            except ConnectionError:
                raise
            except Exception:
                raise ConnectionError("authentication_required") from None

    def logout(self, connection):
        params = self.metadata()
        if not params.get("revocation_endpoint"):
            return {"remote": "unsupported"}
        with self.client(params) as client:
            try:
                response = client.revoke_token(params["revocation_endpoint"], token=connection["credentials"].get("refresh_token") or connection["credentials"]["access_token"], timeout=30, allow_redirects=False)
                if not 200 <= response.status_code < 300:
                    raise ValueError()
            except Exception:
                raise ConnectionError("unavailable") from None
        return {"remote": "revoked"}


class LdapAdapter(Adapter):
    def login(self, inputs, *, cancelled=None, progress=None):
        try:
            import ssl
            import ldap3
            from ldap3.utils.conv import escape_filter_chars
        except ImportError:
            raise ConnectionError("dependency_missing") from None
        check_cancelled(cancelled)
        p = self.params
        username, password = inputs.get("username", ""), inputs.get("password", "")
        if not username or not password or (p.get("bind_dn") and not inputs.get("bind_password")):
            raise ConnectionError("authentication_required")
        tls = ldap3.Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=p.get("ca_file"))
        server = ldap3.Server(p["host"], port=int(p.get("port", 636 if p["tls"] == "ldaps" else 389)),
                             use_ssl=p["tls"] == "ldaps", tls=tls, connect_timeout=10)
        mode = ldap3.AUTO_BIND_TLS_BEFORE_BIND if p["tls"] == "starttls" else ldap3.AUTO_BIND_NO_TLS
        attribute = p.get("identity_attribute", "entryUUID")
        try:
            with ldap3.Connection(server, user=p.get("bind_dn") or username,
                                  password=inputs.get("bind_password") if p.get("bind_dn") else password,
                                  auto_bind=mode, receive_timeout=30, raise_exceptions=True) as search:
                search.search(p["base_dn"], p["user_filter"].replace("{username}", escape_filter_chars(username)), attributes=[attribute])
                if len(search.entries) != 1:
                    raise ConnectionError("authentication_required")
                entry = search.entries[0]
                subject = str(entry[attribute].value or entry.entry_dn)
                dn = entry.entry_dn
            check_cancelled(cancelled)
            with ldap3.Connection(server, user=dn, password=password, auto_bind=mode, receive_timeout=30, raise_exceptions=True):
                pass
        except ConnectionError:
            raise
        except Exception:
            raise ConnectionError("authentication_required") from None
        return result(identity("ldap:" + p["host"] + "/" + p["base_dn"], subject), {}, profile={"label": username, "directory_only": True})

    def headers(self, credentials):
        raise ConnectionError("unsupported_operation")


class WecomAppAdapter(Adapter):
    def login(self, inputs, *, cancelled=None, progress=None):
        check_cancelled(cancelled)
        secret = inputs.get("secret", "")
        if not secret:
            raise ConnectionError("authentication_required")
        data = self.request("GET", "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                            params={"corpid": self.params["corp_id"], "corpsecret": secret})
        if data.get("errcode", 0) or not data.get("access_token"):
            raise ConnectionError("authentication_required")
        # Validate this app's identity; an application token never represents a member.
        app = self.request("GET", "https://qyapi.weixin.qq.com/cgi-bin/agent/get",
                           params={"access_token": data["access_token"], "agentid": self.params["agent_id"]})
        if app.get("errcode", 0) or str(app.get("agentid")) != str(self.params["agent_id"]):
            raise ConnectionError("forbidden")
        return result(identity("https://qyapi.weixin.qq.com", str(app["agentid"]), self.params["corp_id"], "application"),
                      {"access_token": data["access_token"], "secret": secret, "expires_at": time.time() + int(data.get("expires_in", 7200))},
                      profile={"label": str(app.get("name", "企业应用"))})

    def refresh(self, connection):
        return self.login(connection["credentials"])

    def verify(self, connection):
        return self.refresh(connection)

    def headers(self, credentials):
        # WeCom uses an access_token query parameter, injected by the broker only.
        return {}


class _MemoryKnowledgeStore:
    def __init__(self, connection=None):
        self.value = copy.deepcopy(connection)

    def connection(self, *args, secret=False, **kwargs):
        value = copy.deepcopy(self.value)
        if value and not secret:
            value.pop("credentials", None)
        return value

    def save_connection(self, public, credentials, **kwargs):
        self.value = {**copy.deepcopy(public), "credentials": copy.deepcopy(credentials)}


class CloudKnowledgeAdapter(Adapter):
    def provider(self, memory):
        from ..knowledge_sources import LexiangProvider, TencentDocsProvider
        cls = TencentDocsProvider if self.template["adapter"] == "tencent_docs" else LexiangProvider
        return cls(memory)

    def login(self, inputs, *, cancelled=None, progress=None):
        memory = _MemoryKnowledgeStore()
        provider = self.provider(memory)
        try:
            if self.template["adapter"] == "lexiang":
                provider.connect(self.params["company"], inputs.get("token", ""))
            else:
                url = provider.start_authorization()
                confirmed = threading.Event()
                if progress:
                    progress({"stage": "tencent", "url": url, "confirm": confirmed})
                else:
                    raise ConnectionError("unsupported_operation")
                deadline = time.monotonic() + 300
                while not confirmed.wait(0.1):
                    check_cancelled(cancelled)
                    if time.monotonic() >= deadline:
                        raise ConnectionError("authentication_required")
                check_cancelled(cancelled)
                provider.finish_authorization(confirmed=True)
            public = memory.connection(secret=True)
            return result(identity(self.base, public["user_id"], public["tenant_id"]), public["credentials"],
                          profile={k: v for k, v in public.items() if k != "credentials"})
        except ConnectionError:
            raise
        except Exception as exc:
            code = "authentication_required" if getattr(exc, "status", 0) == 401 else "invalid_response"
            raise ConnectionError(code) from None

    def verify(self, connection):
        public = {**connection.get("profile", {}), "credentials": connection["credentials"]}
        provider = self.provider(_MemoryKnowledgeStore(public))
        try:
            if self.template["adapter"] == "lexiang":
                provider.verify()
            else:
                provider._call(public, "manage.folder_list", {})
        except Exception as exc:
            raise ConnectionError("authentication_required" if getattr(exc, "status", 0) == 401 else "unavailable") from None
        return result(connection["identity"], connection["credentials"], profile=connection.get("profile"))

    def headers(self, credentials):
        return {"Authorization": ("" if self.template["adapter"] == "tencent_docs" else "Bearer ") + credentials["token"]}


def adapter_for(template, transport=None):
    classes = {"api_key": ApiKeyAdapter, "oauth2": OAuthAdapter, "wecom_member": OAuthAdapter,
               "ldap": LdapAdapter, "wecom_app": WecomAppAdapter, "superset": SupersetAdapter,
               "weknora": WeKnoraAdapter, "tencent_docs": CloudKnowledgeAdapter, "lexiang": CloudKnowledgeAdapter}
    cls = classes.get(template["adapter"])
    if cls is None:
        raise ConnectionError("unsupported_adapter")
    return cls(template, transport)
