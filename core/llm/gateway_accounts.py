"""User-account adapters for sub2api and New API. No persistent login state."""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import threading
import time
import uuid
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import httpx

from .gateway_catalog import catalog_record, new_catalog_model, normalize_gateway_source

log = logging.getLogger(__name__)


class GatewayError(RuntimeError):
    def __init__(self, message, *, code="request_failed", status=0):
        super().__init__(message)
        self.code, self.status = code, status


def _parse_connection_url(value):
    """Remove browser-only URL parts while retaining reverse-proxy prefixes."""
    try:
        url = urlsplit(str(value or "").strip())
        port = url.port
        if url.scheme not in {"http", "https"} or not url.hostname or url.username is not None or url.password is not None:
            raise ValueError()
        host = url.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        netloc = host + (f":{port}" if port and (url.scheme, port) not in {("https", 443), ("http", 80)} else "")
        path = url.path.rstrip("/")
        # An explicit API suffix can follow a proxy prefix named like a page.
        page = None if path.endswith(("/api/v1", "/v1", "/api")) else re.search(
            r"/(?:dashboard|login|console)(?:/.*)?$", path)
        hash_page = re.match(r"/?(?:dashboard|login|console)(?:[/?]|$)", url.fragment)
        if page:
            path = path[:page.start()].rstrip("/")
        return url._replace(netloc=netloc, path=path, query="", fragment=""), bool(page or hash_page)
    except ValueError:
        raise GatewayError("请填写完整的 HTTP 或 HTTPS 站点地址，不包含内嵌账号或密码。", code="invalid_url") from None


def normalize_site_url(value):
    url, _ = _parse_connection_url(value)
    path = url.path
    for suffix in ("/api/v1", "/v1", "/api"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    return urlunsplit(url._replace(path=path))


def normalize_api_base_url(value, provider_type):
    """Accept copied gateway pages without rewriting explicit custom API paths."""
    url, is_page = _parse_connection_url(value)
    if is_page:
        return normalize_site_url(url.geturl()) + ("/v1" if provider_type == "openai" else "")
    if provider_type == "openai" and not url.path:
        url = url._replace(path="/v1")
    return urlunsplit(url)


def encrypt_password(password, public_key, key_id):
    """Implement the upstream RSA-OAEP and long-password v2 wire contracts."""
    from Crypto.Cipher import AES, PKCS1_OAEP
    from Crypto.Hash import SHA256
    from Crypto.PublicKey import RSA
    from Crypto.Random import get_random_bytes
    b64 = lambda data: base64.b64encode(data).decode("ascii")
    key = RSA.import_key(public_key)
    plaintext = password.encode("utf-8")
    if len(plaintext) <= key.size_in_bytes() - 66:
        ciphertext = b64(PKCS1_OAEP.new(key, hashAlgo=SHA256).encrypt(plaintext))
    else:
        secret, nonce = get_random_bytes(32), get_random_bytes(12)
        wrapped = PKCS1_OAEP.new(key, hashAlgo=SHA256, label=b"password-v2").encrypt(secret)
        cipher = AES.new(secret, AES.MODE_GCM, nonce=nonce)
        cipher.update(f"password-v2:{key_id}".encode())
        encrypted, tag = cipher.encrypt_and_digest(plaintext)
        ciphertext = ".".join(("v2", b64(wrapped), b64(nonce), b64(encrypted + tag)))
    return {"password_encrypted": ciphertext, "encryption_key_id": key_id}


def _full_key(value):
    value = str(value or "").strip()
    return bool(value and not any(marker in value for marker in ("*", "…", "...", "•")))


class GatewayAccount:
    """One HTTP session belongs to one site and one login flow, never shared."""
    def __init__(self, site_url, platform="auto", *, transport=None):
        self.site_url = normalize_site_url(site_url)
        if platform not in {"auto", "sub2api", "new_api"}:
            raise GatewayError("请选择 sub2api 或 New API。")
        self.platform = platform
        self.client = httpx.Client(timeout=httpx.Timeout(20, connect=10), follow_redirects=False,
                                   transport=transport, headers={"User-Agent": "Cowork/ModelConnections"})
        self.cancelled = threading.Event()
        self.public = {}
        self.account_id = ""
        self.challenge = {}
        self.auth_headers = {}
        self.secrets = set()
        self.pending_creation = None
        self.created_keys = []
        self.flow_id = uuid.uuid4().hex[:12]

    def cancel(self):
        self.cancelled.set()

    def close(self):
        self.cancel()
        self.client.cookies.clear()
        self.client.close()
        self.auth_headers.clear()
        self.challenge.clear()
        self.secrets.clear()

    def _check_cancelled(self):
        if self.cancelled.is_set():
            raise GatewayError("操作已取消，已有配置已保留。", code="cancelled")

    def _safe(self, message):
        result = str(message or "站点请求失败")
        for secret in sorted(self.secrets, key=len, reverse=True):
            if secret:
                result = result.replace(secret, "***")
        result = re.sub(r"(?i)(bearer\s+|sk-)[\w.~/+=-]+", r"\1***", result)
        return result[:500]

    def _request(self, method, path, *, body=None, params=None, authenticated=True, api_key=None):
        self._check_cancelled()
        log.info("gateway.request.start flow=%s platform=%s method=%s path=%s", self.flow_id, self.platform, method, path)
        headers = dict(self.auth_headers) if authenticated else {}
        if api_key is not None:
            headers = {"Authorization": f"Bearer {api_key}", "x-api-key": api_key, "anthropic-version": "2023-06-01"}
            self.secrets.add(api_key)
        try:
            request = self.client.build_request(method, self.site_url + path, json=body, params=params, headers=headers)
            if api_key is not None:
                request.headers.pop("Cookie", None)
            response = self.client.send(request)
        except httpx.TimeoutException:
            raise GatewayError("连接超时，已保留当前输入，请重试。", code="timeout") from None
        except httpx.HTTPError:
            raise GatewayError("无法连接站点，请检查地址和网络后重试。", code="network") from None
        self._check_cancelled()
        self.secrets.update(str(cookie.value) for cookie in self.client.cookies.jar)
        log.info("gateway.request.finish flow=%s status=%s path=%s", self.flow_id, response.status_code, path)
        if 300 <= response.status_code < 400:
            raise GatewayError("站点要求跳转，请使用最终站点地址重新连接。", code="redirect", status=response.status_code)
        if response.status_code in {401, 403}:
            raise GatewayError("登录或密钥授权已失效，或站点要求网页验证。请重新登录或前往站点。",
                               code="unauthorized", status=response.status_code)
        if response.status_code == 429:
            raise GatewayError("站点请求过于频繁，请稍后重试。", code="rate_limit", status=429)
        if response.is_error:
            raise GatewayError(f"站点接口请求失败（HTTP {response.status_code}）。", status=response.status_code)
        try:
            if len(response.content) > 8 * 1024 * 1024:
                raise ValueError()
            payload = response.json()
        except (ValueError, TypeError):
            raise GatewayError("站点没有返回有效接口数据；可能需要网页验证，或该版本尚不兼容。", code="invalid_response") from None
        if isinstance(payload, dict):
            if payload.get("success") is False or ("code" in payload and payload["code"] not in (0, 200, "0", "200")):
                raise GatewayError(self._safe(payload.get("message") or payload.get("msg") or "站点拒绝了请求"), code="rejected")
            if "data" in payload:
                return payload["data"]
        return payload

    def probe(self):
        candidates = [("sub2api", "/api/v1/settings/public"), ("new_api", "/api/status")]
        errors = []
        for platform, path in candidates:
            if self.platform not in ("auto", platform):
                continue
            try:
                data = self._request("GET", path, authenticated=False)
                markers = {"registration_enabled", "site_name", "turnstile_enabled", "password_login_enabled"} if platform == "sub2api" else {"version", "system_name", "password_login_enabled", "password_login_encryption_enabled"}
                if not isinstance(data, dict) or not markers.intersection(data):
                    raise GatewayError("未识别到站点接口。")
                self.platform, self.public = platform, data
                log.info("gateway.probe.complete platform=%s", platform)
                return {"platform": platform, "site_url": self.site_url}
            except GatewayError as error:
                if error.code == "cancelled":
                    raise
                errors.append(error)
        raise GatewayError("未能识别站点，请检查地址或手动选择站点类型。" if self.platform == "auto" else str(errors[-1]), code="probe_failed")

    def login(self, username, password):
        if not username.strip() or not password:
            raise GatewayError("请填写账号和密码。")
        self.secrets.add(password)
        if not self.public:
            self.probe()
        if self.public.get("password_login_enabled") is False:
            raise GatewayError("此站点未开放密码登录，请前往站点登录后选择“填写 API Key”。", code="browser_required")
        if any(self.public.get(key) for key in ("turnstile_enabled", "turnstile_check", "TurnstileCheck", "tencent_captcha_enabled")):
            raise GatewayError("此站点启用了网页验证码，请前往站点获取 API Key，再选择“填写 API Key”。", code="browser_required")
        self.auth_headers.clear()
        self.challenge.clear()
        self.account_id = ""
        self.client.cookies.clear()
        if self.platform == "sub2api":
            data = self._request("POST", "/api/v1/auth/login", body={"email": username.strip(), "password": password}, authenticated=False)
        else:
            body = {"username": username.strip(), "password": password}
            if self.public.get("password_login_encryption_enabled"):
                key = self._request("GET", "/api/user/login/encryption-key", authenticated=False)
                try:
                    body = {"username": username.strip(), **encrypt_password(password, key["public_key"], key["kid"])}
                except (KeyError, ValueError, TypeError):
                    raise GatewayError("无法读取站点密码加密信息，请检查站点版本。") from None
            data = self._request("POST", "/api/user/login", body=body, authenticated=False)
        return self._accept_auth(data)

    def _accept_auth(self, data):
        if not isinstance(data, dict):
            raise GatewayError("站点未返回有效的登录信息。")
        if data.get("requires_2fa") or data.get("require_2fa") or data.get("require_verification"):
            methods = data.get("methods", [])
            if methods and not any(item.get("method") == "2fa" and item.get("available") for item in methods if isinstance(item, dict)):
                raise GatewayError("站点要求其他验证方式，请前往站点后手动填写 API Key。", code="browser_required")
            self.challenge = data
            for key in ("temp_token", "flow_token"):
                if data.get(key):
                    self.secrets.add(str(data[key]))
            return {"requires_2fa": True}
        token = data.get("access_token")
        user = data.get("user", data)
        if not isinstance(user, dict) or user.get("id") is None:
            raise GatewayError("站点未返回账号标识，无法继续读取密钥。")
        self.account_id = str(user["id"])
        if token:
            self.secrets.add(str(token))
            self.auth_headers = {"Authorization": f"Bearer {token}"}
        elif self.platform == "new_api" and self.client.cookies:
            self.auth_headers = {"New-Api-User": self.account_id}
        else:
            raise GatewayError("站点未返回有效的登录凭据。")
        if data.get("refresh_token"):
            self.secrets.add(str(data["refresh_token"]))
        self.challenge = {}
        return {"requires_2fa": False, "account_id": self.account_id}

    def complete_2fa(self, code):
        if not self.challenge:
            raise GatewayError("验证会话已失效，请重新登录。")
        self.secrets.add(code)
        if self.platform == "sub2api":
            path, body = "/api/v1/auth/login/2fa", {"temp_token": self.challenge.get("temp_token"), "totp_code": code.strip()}
        elif self.challenge.get("flow_token"):
            path, body = "/api/user/login/verify", {"flow_token": self.challenge["flow_token"], "method": "2fa", "code": code.strip()}
        else:
            path, body = "/api/user/login/2fa", {"code": code.strip()}
        return self._accept_auth(self._request("POST", path, body=body, authenticated=False))

    def _pages(self, path):
        records, seen = [], set()
        for page in range(1, 101):
            params = {"page": page, "page_size": 100} if self.platform == "sub2api" else {"p": page, "page_size": 100}
            data = self._request("GET", path, params=params)
            items = data if isinstance(data, list) else data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, list):
                raise GatewayError("站点密钥列表格式无法识别。")
            fresh = [item for item in items if isinstance(item, dict) and str(item.get("id")) not in seen]
            if items and not fresh:
                raise GatewayError("站点分页重复，已停止读取，请检查站点版本。")
            records.extend(fresh)
            seen.update(str(item.get("id")) for item in fresh)
            total = data.get("total") if isinstance(data, dict) else None
            if not items or (isinstance(total, int) and len(records) >= total) or (total is None and len(items) < 100):
                return records
        raise GatewayError("密钥列表超过分页上限，请缩小账号中的密钥范围。")

    def list_keys(self):
        records = self._pages("/api/v1/keys" if self.platform == "sub2api" else "/api/token/")
        result = []
        for raw in records:
            group = raw.get("group") if isinstance(raw.get("group"), dict) else {}
            state = raw.get("status", "active" if self.platform == "sub2api" else 1)
            valid = state in ("active", 1)
            reason = "" if valid else {"inactive": "已禁用", "quota_exhausted": "额度已用完", "expired": "已过期", 2: "已禁用", 3: "已过期", 4: "额度已用完"}.get(state, "不可用")
            expiry = raw.get("expires_at") if self.platform == "sub2api" else raw.get("expired_time")
            try:
                expires = datetime.fromisoformat(expiry.replace("Z", "+00:00")).timestamp() if isinstance(expiry, str) else float(expiry or -1)
            except (ValueError, TypeError):
                expires = -1
            if expires > 0 and expires <= time.time():
                valid, reason = False, "已过期"
            if self.platform == "sub2api":
                exhausted = float(raw.get("quota") or 0) > 0 and float(raw.get("quota_used") or 0) >= float(raw["quota"])
            else:
                exhausted = raw.get("unlimited_quota") is False and float(raw.get("remain_quota") or 0) <= 0
            if exhausted:
                valid, reason = False, "额度已用完"
            if self.platform == "sub2api" and "quota" in raw:
                quota_label = f"已用 {raw.get('quota_used', 0)} / {raw['quota']} USD" if raw["quota"] else "不另设密钥额度上限"
            elif self.platform == "new_api" and "unlimited_quota" in raw:
                quota_label = "不另设密钥额度上限" if raw["unlimited_quota"] else f"剩余 {raw.get('remain_quota', 0)} 站点额度单位"
            else:
                quota_label = "额度信息未提供"
            result.append({"id": str(raw.get("id", "")), "name": str(raw.get("name") or "未命名密钥"),
                           "group_id": str(raw.get("group_id") or raw.get("group") or "") if not group else str(group.get("id", "")),
                           "group_name": str(group.get("name") or (raw.get("group") if isinstance(raw.get("group"), str) else "默认分组")),
                           "platform": str(group.get("platform") or ""), "usable": valid, "reason": reason,
                           "expires_at": str(expiry or "不过期"), "ip_limited": bool(raw.get("ip_whitelist") or raw.get("allow_ips")),
                           "quota_label": quota_label,
                           "model_limits": str(raw.get("model_limits") or "") if raw.get("model_limits_enabled") else "",
                           "group_restrictions": {key: group[key] for key in ("max_reasoning_effort", "allow_image_generation") if key in group}})
        return result

    def read_key(self, key_id):
        if not str(key_id).isdigit():
            raise GatewayError("密钥标识无效。")
        if self.platform == "sub2api":
            data = self._request("GET", f"/api/v1/keys/{key_id}")
        else:
            data = self._request("GET", f"/api/token/{key_id}")
            if not _full_key(data.get("key")):
                data = self._request("POST", f"/api/token/{key_id}/key")
        key = str(data.get("key") or "").strip()
        if not _full_key(key):
            raise GatewayError("站点未提供完整密钥，请前往站点查看后手动填写。")
        if self.platform == "new_api" and not key.startswith("sk-"):
            key = "sk-" + key
        self.secrets.add(key)
        return key

    def groups(self):
        data = self._request("GET", "/api/v1/groups/available" if self.platform == "sub2api" else "/api/user/self/groups")
        if isinstance(data, list):
            return [{"id": str(item["id"]), "name": str(item.get("name") or item["id"])} for item in data if isinstance(item, dict) and "id" in item]
        if isinstance(data, dict):
            return [{"id": str(key), "name": str(value.get("desc") or key) if isinstance(value, dict) else str(key)} for key, value in data.items()]
        raise GatewayError("无法读取可用分组。")

    def create_key(self, name, group_id="", days=0, quota=0):
        if self.pending_creation:
            raise GatewayError("上一次创建结果尚未确认，请先点击“核对创建结果”。", code="creation_uncertain")
        if not name.strip() or days < 0 or quota < 0:
            raise GatewayError("请填写密钥名称，期限和额度不能小于零。")
        # New API limits token names by UTF-8 byte length, including the suffix.
        unique_name = name.strip().encode("utf-8")[:41].decode("utf-8", errors="ignore") + "-" + uuid.uuid4().hex[:8]
        if self.platform == "sub2api":
            body = {"name": unique_name, "group_id": int(group_id) if group_id else None, "quota": quota}
            if days:
                body["expires_in_days"] = days
            path = "/api/v1/keys"
        else:
            unit = float(self.public.get("quota_per_unit") or 500000)
            body = {"name": unique_name, "group": group_id, "expired_time": int(time.time()) + days * 86400 if days else -1,
                    "unlimited_quota": quota == 0, "remain_quota": int(quota * unit), "model_limits_enabled": False,
                    "allow_ips": "", "cross_group_retry": False}
            path = "/api/token/"
        self.pending_creation = unique_name
        try:
            self._request("POST", path, body=body)
        except GatewayError as error:
            if error.code not in {"network", "timeout", "cancelled", "invalid_response"} and error.status < 500:
                self.pending_creation = None
            raise
        return self.reconcile_creation()

    def reconcile_creation(self):
        name = self.pending_creation
        if not name:
            raise GatewayError("没有待核对的创建操作。")
        keys = self.list_keys()
        matching = [key for key in keys if key["name"] == name]
        if len(matching) != 1:
            raise GatewayError("尚未找到唯一的创建结果，请稍后再次核对或前往站点查看；不会重复创建。", code="creation_uncertain")
        self.created_keys.append(matching[0])
        self.pending_creation = None
        return matching[0]

    def discover(self, key_info):
        key = self.read_key(key_info["id"])
        records = self.discover_with_key(key, key_info)
        return {"key": key, "key_info": key_info, "records": records, "source": self.source(key_info)}

    def discover_with_key(self, key, key_info=None):
        """Read only API-key-scoped lists; optional metadata failures stay visible."""
        key_info = key_info or {}
        data = self._request("GET", "/v1/models", api_key=key, authenticated=False)
        if not isinstance(data, list):
            raise GatewayError("模型接口没有返回有效模型列表。")
        metadata, warning = {}, ""
        if self.platform == "sub2api" and data:
            try:
                manifest = self._request("GET", "/v1/models", params={"client_version": "0.0.0"}, api_key=key, authenticated=False)
                if isinstance(manifest, dict) and isinstance(manifest.get("models"), list):
                    metadata = {str(item.get("slug") or item.get("id") or ""): item for item in manifest["models"] if isinstance(item, dict)}
                elif not isinstance(manifest, list):
                    raise GatewayError("站点未提供可识别的能力清单。")
            except GatewayError as error:
                if error.code == "cancelled":
                    raise
                warning = "模型列表已保留；能力清单读取未完成：" + str(error)
                log.info("gateway.metadata.unavailable flow=%s code=%s", self.flow_id, error.code)
        provider = "anthropic" if key_info.get("platform") == "anthropic" else "openai"
        records = []
        for item in data:
            if not isinstance(item, dict):
                continue
            extra = metadata.get(str(item.get("id")), {})
            combined = {**item, **extra, "id": item.get("id")}
            if extra and not combined.get("supported_endpoint_types"):
                combined["supported_endpoint_types"] = ["openai-response"]
            record = catalog_record(combined, source=self.platform + (":models+manifest" if extra else ":models"),
                                    default_provider=provider, platform=key_info.get("platform", ""))
            record["discovery"]["group_restrictions"] = key_info.get("group_restrictions", {})
            if warning:
                record["discovery"]["metadata_warning"] = warning
            records.append(record)
        records = list({item["id"]: item for item in records if item["id"]}.values())
        return records

    def source(self, key_info):
        identity = f"{self.platform}|{self.site_url}|{self.account_id}"
        return normalize_gateway_source({"platform": self.platform, "site_url": self.site_url, "account_id": self.account_id,
                                         "key_id": key_info["id"], "key_name": key_info["name"], "group_id": key_info.get("group_id", ""),
                                         "group_name": key_info.get("group_name", ""),
                                         "group_platform": key_info.get("platform", ""),
                                         "service_id": "gateway-" + hashlib.sha256(identity.encode()).hexdigest()[:16]})


def discover_saved_gateway(source, api_key, *, base_url, cancelled=None):
    """Refreshing a saved connection never logs into the management API."""
    account = GatewayAccount(base_url, source["platform"])
    if cancelled is not None:
        account.cancelled = cancelled
    try:
        return account.discover_with_key(api_key, {"platform": source.get("group_platform", "")})
    finally:
        account.close()


def channels_from_selection(results, selected, display_name):
    """Resolve UI selection to ordinary channels, keeping credentials separate."""
    channels = []
    for key_id, result in results.items():
        source = result["source"]
        for provider in ("openai", "anthropic"):
            records = [record for record in result["records"] if (key_id, record["id"]) in selected
                       and record["provider_type"] == provider and not record["discovery"].get("unsupported_reason")]
            if not records:
                continue
            channel_id = f"{source['service_id']}-{key_id}-{provider}"
            url = source["site_url"] + ("/v1" if provider == "openai" else "")
            channels.append({"channel_id": channel_id, "display_name": display_name, "provider_type": provider,
                             "base_url": url, "api_key": result["key"], "gateway_source": source,
                             "models": [new_catalog_model(record, channel_id, url) for record in records]})
    return channels
