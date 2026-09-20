"""Local, declarative deployment configuration. Loading never performs I/O to a service."""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from urllib.parse import urlsplit, parse_qsl

from .errors import ConnectionError

FORMAT_VERSION = 1
ADAPTER_FIELDS = {
    "api_key": {"header", "prefix", "verify_url", "identity_field", "tenant_field"},
    "oauth2": {"issuer", "discovery_url", "authorization_endpoint", "token_endpoint", "jwks_uri",
               "userinfo_endpoint", "revocation_endpoint", "device_authorization_endpoint", "client_id",
               "scope", "audience", "flow", "redirect_port", "oidc", "tenant_claim"},
    "wecom_member": {"issuer", "discovery_url", "authorization_endpoint", "token_endpoint", "jwks_uri",
                     "userinfo_endpoint", "revocation_endpoint", "client_id", "scope", "audience",
                     "redirect_port", "oidc", "tenant_claim"},
    "ldap": {"host", "port", "tls", "base_dn", "user_filter", "bind_dn", "ca_file", "identity_attribute"},
    "wecom_app": {"corp_id", "agent_id"},
    "superset": {"provider", "mcp_url"},
    "weknora": set(),
    "tencent_docs": set(),
    "lexiang": {"company"},
}
COMMON = {"id", "version", "name", "company", "service", "base_url", "adapter", "parameters",
          "suggested_capabilities", "locked"}
SECRET_FIELDS = {"password", "token", "access_token", "refresh_token", "id_token", "secret",
                 "client_secret", "cookie", "authorization", "credentials", "protected_value"}


def safe_url(value, *, local_http=True):
    if not isinstance(value, str):
        raise ConnectionError("invalid_template")
    parts = urlsplit(value)
    if (parts.username or parts.password or parts.fragment or not parts.hostname
            or parts.scheme not in {"https", "http"}
            or (parts.scheme == "http" and (not local_http or parts.hostname not in {"localhost", "127.0.0.1", "::1"}))):
        raise ConnectionError("invalid_template")
    if any(k.lower() in SECRET_FIELDS for k, _ in parse_qsl(parts.query)):
        raise ConnectionError("invalid_template")
    try:
        parts.port
    except ValueError:
        raise ConnectionError("invalid_template") from None
    return value.rstrip("/")


def reject_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in SECRET_FIELDS:
                raise ConnectionError("invalid_template")
            reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            reject_secrets(item)


def validate_template(value):
    if not isinstance(value, dict) or set(value) - COMMON:
        raise ConnectionError("invalid_template")
    reject_secrets(value)
    item = copy.deepcopy(value)
    if (not isinstance(item.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", item["id"])
            or type(item.get("version")) is not int or item["version"] < 1
            or not all(isinstance(item.get(k), str) and item[k].strip() for k in ("name", "service", "adapter"))):
        raise ConnectionError("invalid_template")
    adapter = item["adapter"]
    if adapter not in ADAPTER_FIELDS:
        raise ConnectionError("unsupported_adapter")
    params = item.setdefault("parameters", {})
    if not isinstance(params, dict) or set(params) - ADAPTER_FIELDS[adapter]:
        raise ConnectionError("invalid_template")
    if "base_url" in item:
        item["base_url"] = safe_url(item["base_url"])
    elif adapter != "ldap":
        raise ConnectionError("invalid_template")
    for key, val in params.items():
        if key.endswith(("_endpoint", "_url")) or key in {"issuer", "jwks_uri"}:
            safe_url(val)
    if adapter == "api_key":
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", params.get("header", "Authorization")):
            raise ConnectionError("invalid_template")
        if any(c in params.get("prefix", "Bearer ") for c in "\r\n"):
            raise ConnectionError("invalid_template")
        if params.get("verify_url") and urlsplit(params["verify_url"]).netloc != urlsplit(item["base_url"]).netloc:
            raise ConnectionError("invalid_template")
    if adapter in {"oauth2", "wecom_member"}:
        if not params.get("client_id") or not (params.get("issuer") or params.get("token_endpoint")):
            raise ConnectionError("invalid_template")
        if params.get("flow", "authorization_code") not in {"authorization_code", "device_code", "client_credentials"}:
            raise ConnectionError("invalid_template")
        if adapter == "wecom_member" and not params.get("oidc", True):
            raise ConnectionError("invalid_template")
        if "scope" in params and not isinstance(params["scope"], str):
            raise ConnectionError("invalid_template")
        port = params.get("redirect_port", 0)
        if type(port) is not int or not 0 <= port <= 65535:
            raise ConnectionError("invalid_template")
    if adapter == "ldap":
        if (not params.get("host") or params.get("tls") not in {"ldaps", "starttls"}
                or "{username}" not in params.get("user_filter", "") or not params.get("base_dn")):
            raise ConnectionError("invalid_template")
    if adapter == "superset" and params.get("provider", "db") not in {"db", "ldap"}:
        raise ConnectionError("invalid_template")
    if adapter == "wecom_app" and (not params.get("corp_id") or not params.get("agent_id")
                                  or item.get("base_url") != "https://qyapi.weixin.qq.com"):
        raise ConnectionError("invalid_template")
    if adapter == "tencent_docs" and item.get("base_url") != "https://docs.qq.com/openapi/mcp":
        raise ConnectionError("invalid_template")
    if adapter == "lexiang" and (item.get("base_url") != "https://mcp.lexiang-app.com/mcp" or not params.get("company")):
        raise ConnectionError("invalid_template")
    suggestions = item.setdefault("suggested_capabilities", [])
    if not isinstance(suggestions, list) or any(not isinstance(x, str) or not x for x in suggestions):
        raise ConnectionError("invalid_template")
    if type(item.setdefault("locked", False)) is not bool:
        raise ConnectionError("invalid_template")
    return item


def read_bundle(path):
    if not os.path.exists(path):
        return [], []
    try:
        with open(path, encoding="utf-8-sig") as stream:
            payload = json.load(stream)
        if (not isinstance(payload, dict) or set(payload) != {"version", "templates"}
                or payload["version"] != FORMAT_VERSION or not isinstance(payload["templates"], list)):
            raise ValueError()
    except (ValueError, OSError):
        return [], [{"file": os.path.basename(path), "code": "invalid_template"}]
    valid, errors, ids = [], [], set()
    for index, raw in enumerate(payload["templates"]):
        try:
            item = validate_template(raw)
            if item["id"] in ids:
                raise ConnectionError("template_conflict")
            ids.add(item["id"])
            valid.append(item)
        except (ConnectionError, TypeError, ValueError):
            errors.append({"file": os.path.basename(path), "index": index, "code": "invalid_template"})
    # Duplicate IDs invalidate all entries of that ID, never first-wins.
    duplicates = {x.get("id") for x in payload["templates"] if isinstance(x, dict) and isinstance(x.get("id"), str)
                  and sum(isinstance(y, dict) and y.get("id") == x.get("id") for y in payload["templates"]) > 1}
    return [x for x in valid if x["id"] not in duplicates], errors


class TemplateCatalog:
    def __init__(self, base_dir, data_dir):
        self.distributed_path = os.path.join(base_dir, "connection_templates.json")
        self.local_path = os.path.join(data_dir, "connection_templates.local.json")

    def load(self):
        distributed, errors = read_bundle(self.distributed_path)
        local, extra = read_bundle(self.local_path)
        errors.extend(extra)
        result = {x["id"]: {**x, "origin": "distribution"} for x in distributed}
        for item in local:
            prior = result.get(item["id"])
            if prior and (prior["locked"] or item["version"] < prior["version"]):
                errors.append({"id": item["id"], "code": "template_conflict"})
                continue
            if prior and item["version"] == prior["version"] and item != {k: v for k, v in prior.items() if k != "origin"}:
                errors.append({"id": item["id"], "code": "template_conflict"})
                del result[item["id"]]
                continue
            result[item["id"]] = {**item, "origin": "local"}
        return list(result.values()), errors

    def get(self, template_id):
        for item in self.load()[0]:
            if item["id"] == template_id:
                return {k: v for k, v in item.items() if k != "origin"}
        raise ConnectionError("invalid_template")

    def import_file(self, path, *, replace=False):
        incoming, errors = read_bundle(path)
        if errors or not incoming:
            raise ConnectionError("invalid_template")
        self.import_templates(incoming, replace=replace)

    def import_templates(self, incoming, *, replace=False):
        incoming = [validate_template(item) for item in incoming]
        if len({item["id"] for item in incoming}) != len(incoming):
            raise ConnectionError("template_conflict")
        current, current_errors = self.load()
        local, local_errors = read_bundle(self.local_path)
        if local_errors:
            raise ConnectionError("invalid_template")
        by_id = {x["id"]: x for x in current}
        local_ids = {x["id"]: x for x in local}
        for item in incoming:
            prior = by_id.get(item["id"])
            if prior and prior["locked"]:
                raise ConnectionError("locked_template")
            if prior and (not replace or item["version"] <= prior["version"]):
                raise ConnectionError("template_conflict")
            # Local imports cannot establish an administrator lock.
            item["locked"] = False
            local_ids[item["id"]] = item
        self.write(self.local_path, list(local_ids.values()))

    def remove_local(self, template_id):
        items, errors = read_bundle(self.local_path)
        if errors:
            raise ConnectionError("invalid_template")
        self.write(self.local_path, [x for x in items if x["id"] != template_id])

    @staticmethod
    def write(path, templates):
        payload = {"version": FORMAT_VERSION, "templates": [validate_template(x) for x in templates]}
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=parent, prefix=".connections-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
