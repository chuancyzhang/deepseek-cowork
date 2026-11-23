"""Read-only access to released capabilities under the current enterprise identity."""
from __future__ import annotations

import hashlib
import json
import re
import time
from urllib.parse import urlsplit

from core.enterprise_client import EnterpriseClient, EnterpriseError, MODEL_PREFIX

_CREDENTIAL_TABLE = """CREATE TABLE IF NOT EXISTS capability_credentials (
    platform TEXT NOT NULL, member TEXT NOT NULL, capability TEXT NOT NULL,
    endpoint TEXT NOT NULL, secret BLOB NOT NULL,
    PRIMARY KEY (platform, member, capability, endpoint)
)"""
_SELECTION = re.compile(r"(?:enterprise\.[a-f0-9]{12}\.|mcp-server-enterprise_[a-f0-9]{12}_)[a-f0-9]{32}")


def identity(state):
    connection = state.get("connection") or {}
    member = state.get("member") or {}
    return {"platform_id": connection.get("platform_id", ""), "member_id": member.get("id", ""),
            "generation": state.get("generation", "")}


def selection_key(kind, capability_id, binding):
    scope = hashlib.sha256(
        (binding["platform_id"] + "\0" + binding["member_id"]).encode("utf-8")
    ).hexdigest()[:12]
    if kind == "mcp":
        from core.mcp_client import build_mcp_skill_name
        return build_mcp_skill_name(f"enterprise_{scope}_{capability_id}")
    return f"enterprise.{scope}.{capability_id}"


def is_enterprise_selection(key):
    return bool(_SELECTION.fullmatch(str(key or "")))


def prompt_scope(manifest):
    """Identify synthetic instructions reusable under the same authorized selection."""
    if manifest is None:
        return ""
    binding = manifest.get("binding") if isinstance(manifest, dict) else None
    items = manifest.get("items") if isinstance(manifest, dict) else None
    if (not isinstance(binding, dict) or not all(isinstance(binding.get(key), str) and binding[key].strip()
            for key in ("platform_id", "member_id", "generation", "data_dir"))
            or not isinstance(items, list) or not 0 < len(items) <= 250):
        raise EnterpriseError("企业能力运行信息不完整，请返回聊天重新发送。")
    versions = []
    for item in items:
        if (not isinstance(item, dict) or not isinstance(item.get("id"), str)
                or not re.fullmatch(r"[a-f0-9]{32}", item["id"])
                or item.get("kind") not in ("skill", "mcp", "expert")
                or type(item.get("version")) is not int or not 0 < item["version"] <= 2_147_483_647):
            raise EnterpriseError("企业能力运行信息不完整，请返回聊天重新发送。")
        versions.append((item["id"], item["kind"], item["version"]))
    if len({entry[0] for entry in versions}) != len(versions):
        raise EnterpriseError("企业能力运行信息存在重复版本，请返回聊天重新发送。")
    value = [binding["platform_id"], binding["member_id"], sorted(versions)]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def scope_prompt_messages(messages, scope):
    """Filter only scoped synthetic content in a provider view of history."""
    visible = []
    for message in messages or []:
        meta = message.get("meta") if isinstance(message, dict) else None
        if (isinstance(meta, dict) and "enterprise_prompt_scope" in meta
                and (not scope or meta["enterprise_prompt_scope"] != scope)):
            continue
        visible.append(message)
    return visible


class EnterpriseCapabilities:
    def __init__(self, client: EnterpriseClient):
        self.client = client

    def session(self, binding):
        state = self.client.store.read(secret=True)
        current = identity(state)
        if (not all(current.values()) or any(current[key] != binding.get(key) for key in current)
                or not state.get("access_token") or state.get("expires_at", 0) <= time.time()):
            raise EnterpriseError("企业身份已改变或登录已过期，请使用原账号登录后重试。")
        return state

    def request(self, binding, method, path, *, payload=None):
        state = self.session(binding)
        value = self.client.request(state["connection"], method, path, token=state["access_token"], payload=payload)
        self.session(binding)
        return value

    def catalog(self, binding):
        result = self.request(binding, "GET", "/api/v1/capabilities")
        rows = result.get("items")
        if not isinstance(rows, list):
            raise EnterpriseError("企业平台返回的能力目录无效，请联系管理员。")
        return rows

    def check(self, binding, items, *, include_config=False):
        result = self.request(binding, "POST", "/api/v1/capabilities/check",
                              payload={"items": items, "include_config": include_config})
        if result.get("allowed") is not True:
            raise EnterpriseError("企业能力已停用或撤销授权，输入和已有结果仍保留。")
        if include_config:
            rows = result.get("items")
            expected = {(item["id"], item["kind"], item["version"]) for item in items}
            if not isinstance(rows, list) or len(rows) != len(expected) or {
                    (row["id"], row["kind"], row["version"]) for row in rows} != expected:
                raise EnterpriseError("企业平台未返回本次任务选定的能力版本，请重试。")
            return rows

    def selection(self, skills, expert_key, *, model_binding=None, space_binding=None):
        selected = [name for name in skills if is_enterprise_selection(name)]
        has_expert = is_enterprise_selection(expert_key)
        if not selected and not has_expert:
            return None, None, []
        binding = identity(self.client.store.read())
        for other in (model_binding, space_binding):
            if other and any(binding.get(key) != other.get(key) for key in ("platform_id", "member_id")):
                raise EnterpriseError("所选模型、团队空间和企业能力必须属于同一个企业账号。")
        if model_binding and model_binding.get("generation") != binding["generation"]:
            raise EnterpriseError("企业模型的登录信息已改变，请重新选择模型。输入已保留。")
        rows = self.catalog(binding)
        by_selection = {selection_key(row["kind"], row["id"], binding): row for row in rows}
        by_id = {row["id"]: row for row in rows}
        items = {}

        def add(row, kinds):
            if not row or row["kind"] not in kinds or row.get("available") is False:
                raise EnterpriseError("所选企业能力不可用或授权已撤销。输入已保留，请刷新后重新选择。")
            items[row["id"]] = {key: row[key] for key in ("id", "kind", "version")}
            if row["kind"] == "mcp" and row["config"].get("auth_type") == "bearer" and not self._credential(binding, row):
                raise EnterpriseError(f"请先在企业 MCP 标签页为“{row['name']}”填写访问令牌。")
            return row

        for name in selected:
            add(by_selection.get(name), {"skill", "mcp"})
        expert, dependencies = None, []
        if has_expert:
            expert = add(by_selection.get(expert_key), {"expert"})
            for field, kind in (("skill_ids", "skill"), ("mcp_ids", "mcp")):
                for item_id in expert["config"].get(field, []):
                    row = add(by_id.get(item_id), {kind})
                    dependencies.append(selection_key(kind, row["id"], binding))
        self.session(binding)
        return {"binding": {**binding, "data_dir": str(self.client.store.path.parent)},
                "items": sorted(items.values(), key=lambda item: item["id"])}, expert, dependencies

    def credential(self, binding, item):
        self.session(binding)
        if urlsplit(item["config"]["url"]).scheme != "https":
            raise EnterpriseError("企业 MCP 必须使用 HTTPS 后才能发送访问令牌。")
        value = self._credential(binding, item, decrypt=True)
        if not value:
            raise EnterpriseError(f"请先在企业 MCP 标签页为“{item['name']}”填写访问令牌。")
        return value

    @staticmethod
    def _credential_index(binding, item):
        return (binding["platform_id"], binding["member_id"], item["id"], item["config"]["url"])

    def _credential(self, binding, item, *, decrypt=False):
        store = self.client.store
        with store.db() as db:
            db.execute(_CREDENTIAL_TABLE)
            row = db.execute("SELECT secret FROM capability_credentials WHERE platform=? AND member=? AND capability=? AND endpoint=?",
                             self._credential_index(binding, item)).fetchone()
        if not row:
            return None
        if not decrypt:
            return True
        try:
            return store.crypt().unprotect(row[0]).decode("utf-8")
        except Exception:
            raise EnterpriseError("企业 MCP 凭据无法读取，请重新填写。") from None

    def set_credential(self, key, value):
        store = self.client.store
        binding = identity(store.read())
        if not isinstance(value, str) or len(value) > 8192 or any(ord(char) < 32 or ord(char) > 126 for char in value):
            raise ValueError("请输入有效的 MCP 访问令牌。")
        value = value.strip()
        item = next((row for row in self.catalog(binding)
                     if row["kind"] == "mcp" and selection_key("mcp", row["id"], binding) == key), None)
        if not item or item["config"].get("auth_type") != "bearer":
            raise EnterpriseError("当前企业 MCP 不可用或不接受访问令牌。")
        if value and urlsplit(item["config"]["url"]).scheme != "https":
            raise EnterpriseError("企业 MCP 必须使用 HTTPS 后才能配置访问令牌。")
        self.session(binding)
        index = self._credential_index(binding, item)
        with store.db() as db:
            db.execute(_CREDENTIAL_TABLE)
            current = db.execute("SELECT public FROM connection WHERE id=1").fetchone()
            if not current or identity(json.loads(current[0])) != binding:
                raise EnterpriseError("企业账号已改变，请重新配置 MCP 凭据。")
            if value:
                db.execute("INSERT OR REPLACE INTO capability_credentials VALUES (?, ?, ?, ?, ?)",
                           (*index, store.crypt().protect(value.encode("utf-8"))))
            else:
                db.execute("DELETE FROM capability_credentials WHERE platform=? AND member=? AND capability=? AND endpoint=?", index)
        return {"saved": True}

    def inventory(self):
        state = self.client.store.read()
        binding = identity(state)
        response = {**binding, "platform_name": (state.get("connection") or {}).get("platform_name", ""), "items": []}
        if not binding["member_id"]:
            return {**response, "status": "signed_out"}
        if state.get("expires_at", 0) <= time.time():
            return {**response, "status": "expired"}
        info = self.request(binding, "GET", "/api/v1/platform-info")
        if info.get("platform_id") != binding["platform_id"]:
            raise EnterpriseError("企业标识与当前连接不一致，请重新连接。")
        if not info.get("enterprise_capabilities"):
            return {**response, "status": "unsupported"}
        rows = self.catalog(binding)
        model_ids = {model["id"] for model in state.get("models", [])}
        unavailable_mcp = set()
        for row in rows:
            item = {key: row[key] for key in ("id", "kind", "name", "description", "version")}
            item.update(available=row.get("available", True), selection_key=selection_key(row["kind"], row["id"], binding))
            if row.get("reason"):
                item["reason"] = row["reason"]
            config = row.get("config") or {}
            model_id = config.get("default_model_id") if row["kind"] == "expert" else None
            if model_id in model_ids:
                item["default_model_id"] = f"{MODEL_PREFIX}{binding['platform_id']}:{model_id}"
            if row["kind"] == "mcp" and config.get("auth_type") == "bearer":
                configured = bool(self._credential(binding, row))
                item.update(requires_credential=True, credential_configured=configured,
                            endpoint_host=urlsplit(config["url"]).netloc, credential_label=config.get("credential_variable", ""),
                            available=item["available"] and configured)
                if not item["available"]:
                    item.setdefault("reason", "请为此企业 MCP 配置访问令牌。")
                    unavailable_mcp.add(item["id"])
            response["items"].append(item)
        for row, item in zip(rows, response["items"]):
            if row["kind"] == "expert" and unavailable_mcp.intersection((row.get("config") or {}).get("mcp_ids", [])):
                item["available"] = False
                item.setdefault("reason", "请先为该专家关联的企业 MCP 配置访问令牌。")
        self.session(binding)
        return {**response, "status": "ready"}
