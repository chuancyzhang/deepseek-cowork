"""Install the authorized versions selected for one enterprise chat run."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlsplit

from core.enterprise_capabilities import EnterpriseCapabilities, selection_key
from core.enterprise_client import EnterpriseClient, EnterpriseError, EnterpriseStore
from core.tool_registry import TOOL_SOURCE_USER_EXTENSION


log = logging.getLogger(__name__)
REFERENCE_SCHEME = "cowork-skill://"
_CAPABILITY_ID = re.compile(r"[a-f0-9]{32}")
_INVALID_FILE_NAME = re.compile(r'[\\<>:"|?*\x00-\x1f]')
_RESERVED_FILE_NAME = re.compile(r"(?i)(CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?")


def resource_parts(relative):
    parts = relative.split("/")
    if (len(relative) > 200 or len(parts) < 2 or parts[0] not in {"references", "scripts", "assets"}
            or any(not part or part in {".", ".."} or part.endswith((" ", "."))
                   or _INVALID_FILE_NAME.search(part) or _RESERVED_FILE_NAME.fullmatch(part) for part in parts)):
        raise EnterpriseError("企业 Skill 包的资源路径无效。")
    return parts


class EnterpriseCapabilityRuntime:
    def __init__(self, manifest, config_manager):
        self.binding = dict(manifest.get("binding") or {})
        self.items = list(manifest.get("items") or [])
        if (not all(isinstance(self.binding.get(key), str) and self.binding[key]
                    for key in ("platform_id", "member_id", "generation", "data_dir"))
                or not self.items or Path(self.binding["data_dir"]).resolve() != Path(config_manager.data_dir).resolve()
                or any(not isinstance(item, dict) or not isinstance(item.get("id"), str)
                       or not _CAPABILITY_ID.fullmatch(item["id"]) for item in self.items)):
            raise EnterpriseError("本轮企业能力的账号或版本无效，请重新选择后发送。")
        self.api = EnterpriseCapabilities(EnterpriseClient(EnterpriseStore(self.binding["data_dir"])))
        rows = self.api.check(self.binding, self.items, include_config=True)
        self.closed = False
        self.temp = None
        self.skills = {}
        self.mcps = {}
        try:
            for item in sorted(rows, key=lambda row: (row["kind"], row["id"])):
                key = selection_key(item["kind"], item["id"], self.binding)
                if item["kind"] == "skill":
                    self.skills[key] = self._materialize_skill(key, item)
                elif item["kind"] == "mcp":
                    self.mcps[key] = self._mcp_configuration(key, item), item
        except Exception:
            self.close()
            raise

    def check(self):
        if self.closed:
            raise EnterpriseError("企业任务已结束，本次操作未执行。")
        self.api.check(self.binding, self.items)

    def _materialize_skill(self, key, item):
        if self.temp is None:
            scratch = Path(self.binding["data_dir"]) / "enterprise-run-resources"
            scratch.mkdir(parents=True, exist_ok=True)
            self.temp = tempfile.TemporaryDirectory(prefix="skill-", dir=scratch)
        root = Path(self.temp.name) / key
        root.mkdir()
        config = item["config"]
        resources = config.get("files", {})
        references = []
        has_scripts = False
        (root / "SKILL.md").write_text(f"---\nname: {key}\n---\n{config['instructions']}\n", encoding="utf-8")
        for name, content in sorted(resources.items()):
            parts = resource_parts(name)
            path = root.joinpath(*parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as output:
                output.write(content)
            if parts[0] in {"references", "assets"}:
                references.append(name)
            elif parts[0] == "scripts":
                has_scripts = True
        spec = {"name": key, "kind": "knowledge", "description": item["description"],
                "source_format": "enterprise", "references": references,
                "tool_refs": (["text_file_read"] if references else []) + (["run_skill_script"] if has_scripts else [])}
        (root / "skill.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        return root, item

    @staticmethod
    def _mcp_configuration(key, item):
        config = item["config"]
        url = config["url"]
        address = urlsplit(url)
        if (config.get("transport") != "streamable_http" or not address.hostname
                or address.username is not None or address.password is not None or address.query or address.fragment
                or "\\" in url or any(char.isspace() or ord(char) < 32 for char in url)
                or address.scheme not in {"http", "https"} or config.get("auth_type") not in {"none", "bearer"}
                or (config["auth_type"] == "bearer" and address.scheme != "https")):
            raise EnterpriseError("企业 MCP 服务地址或认证方式无效。")
        return {"id": key.removeprefix("mcp-server-"), "name": item["name"], "url": url,
                "transport": "streamable_http", "enabled": True, "redact_errors": True,
                "follow_redirects": False, "enterprise": True, "allowed_tools": list(config.get("allowed_tools", []))}

    def mcp_request(self, key):
        scoped = self.mcps.get(key)
        if scoped is None:
            return None
        config, item = scoped

        def prepare():
            self.check()
            request = dict(config)
            if item["config"]["auth_type"] == "bearer":
                request["headers"] = {"Authorization": "Bearer " + self.api.credential(self.binding, item)}
                self.api.session(self.binding)
            return request

        return prepare

    def reference(self, path):
        key, _, encoded = path.removeprefix(REFERENCE_SCHEME).partition("/")
        relative = unquote(encoded)
        if key not in self.skills:
            raise EnterpriseError("当前任务未加载此企业 Skill。")
        root, item = self.skills[key]
        if relative not in item["config"].get("files", {}):
            raise EnterpriseError("此企业 Skill 中没有该文件。")
        parts = resource_parts(relative)
        if parts[0] == "scripts" or not root.joinpath(*parts).resolve().is_relative_to(root.resolve()):
            raise EnterpriseError("此文件不可作为企业 Skill 参考资料读取。")
        self.check()
        return str(root), relative

    def install(self, manager):
        manager.enterprise_capability_runtime = self
        for key, (root, item) in self.skills.items():
            if manager.loaded_skill_sources.get(key) == str(root):
                continue
            if key in manager.skill_records:
                raise EnterpriseError("本机 Skill 使用了企业专用标识，请更改本机 Skill 标识。")
            manager.loaded_skill_sources[key] = str(root)
            manager.skill_source_kinds[key] = TOOL_SOURCE_USER_EXTENSION
            record = manager._load_skill_record(key, str(root))
            record["meta"]["display_name"] = item["name"]
            manager._remember_run_skill(record)
        for key, (config, item) in self.mcps.items():
            current = manager.skill_records.get(key)
            if current and current.get("enterprise_version") == item["version"]:
                if key not in manager.loaded_skills_meta:
                    manager._remember_run_skill(current)
                continue
            if current:
                raise EnterpriseError("本机 MCP 使用了企业专用标识，请更改本机 MCP 标识。")
            record = manager._build_mcp_skill_record(key, config, [], {"ok": True, "message": "企业 MCP 已就绪。"})
            record["enterprise_version"] = item["version"]
            manager._remember_run_skill(record)

    def close(self):
        self.closed = True
        if self.temp is not None:
            try:
                self.temp.cleanup()
            except OSError as exc:
                log.warning("enterprise.skills_cleanup_failed error_type=%s", type(exc).__name__)
            self.temp = None
