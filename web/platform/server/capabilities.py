"""Publish enterprise Skill, MCP and expert configurations and check member grants."""
from __future__ import annotations

import ast
import hashlib
import logging
import re
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response
from sqlalchemy import Boolean, Float, ForeignKey, JSON, String, Text, select, tuple_, update
from sqlalchemy.orm import Mapped, defer, mapped_column
from starlette.concurrency import run_in_threadpool

from .capability_packages import MAX_ZIP_BYTES, capability_zip_template, invalid_package, read_capability_package
from .database import Base, Member, Model, uid
from .departments import ancestors, checked_units
from .security import PlatformError

log = logging.getLogger(__name__)
KINDS = {"skill", "mcp", "expert"}
IDENTIFIER = re.compile(r"[a-f0-9]{32}")
RESOURCE_PATH = re.compile(r"(?:references|scripts|assets)/[^/]+(?:/[^/]+)*")
MAX_CONFIG_BYTES = 1024 * 1024


class Capability(Base):
    __tablename__ = "enterprise_capabilities"
    department_ids: Mapped[list] = mapped_column(JSON, default=list)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(16))
    draft_name: Mapped[str] = mapped_column(String(128))
    draft_description: Mapped[str] = mapped_column(Text)
    draft_config: Mapped[dict] = mapped_column(JSON)
    draft_tested: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(default=1)
    current_version: Mapped[int] = mapped_column(default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    all_members: Mapped[bool] = mapped_column(Boolean, default=False)
    member_ids: Mapped[list] = mapped_column(JSON, default=list)


class CapabilityVersion(Base):
    __tablename__ = "enterprise_capability_versions"
    capability_id: Mapped[str] = mapped_column(ForeignKey(Capability.id), primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSON)
    published_at: Mapped[float] = mapped_column(Float, default=time.time)


def invalid(message):
    raise PlatformError("invalid_capability", message)


def string(value, label, maximum, *, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        invalid(f"{label}无效或超过长度限制。")
    try:
        value.encode("utf-8")
    except UnicodeError:
        invalid(f"{label}包含无效字符。")
    return value


def string_list(values, label, *, size=100, element_size=256, identifiers=False):
    if not isinstance(values, list) or len(values) > size:
        invalid(f"{label}必须是列表，且不能超过 {size} 项。")
    result = []
    for value in values:
        value = string(value, label, element_size, required=True).strip()
        if identifiers and not IDENTIFIER.fullmatch(value):
            invalid(f"{label}包含无效标识。")
        result.append(value)
    if len(set(result)) != len(result):
        invalid(f"{label}有重复项。")
    return sorted(result)


def normalize_files(values):
    if not isinstance(values, dict) or len(values) > 100:
        invalid("Skill 资源必须是包含至多 100 个文件的对象。")
    files, names = {}, set()
    size = 0
    for path, content in sorted(values.items()):
        if (len(path) > 200 or not RESOURCE_PATH.fullmatch(path)
                or any(part in {".", ".."} or part.endswith((".", " "))
                       or re.search(r'[\\<>:"|?*\x00-\x1f]', part)
                       or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
                       for part in path.split("/"))):
            invalid("Skill 资源路径应位于 references/、scripts/ 或 assets/ 中。")
        string(path, "资源路径", 200)
        if path.casefold() in names:
            invalid("Skill 资源路径存在重名。")
        names.add(path.casefold())
        files[path] = string(content, "资源内容", 384 * 1024)
        size += len(files[path].encode("utf-8"))
    if any(any("/".join(name.split("/")[:i]) in names for i in range(1, name.count("/") + 1))
           for name in names):
        invalid("Skill 资源的文件和目录路径冲突。")
    if size > 384 * 1024:
        invalid("Skill 文本资源不能超过 384 KiB。")
    return files


def config_keys(config, allowed):
    if set(config) - allowed:
        invalid("包含不支持的能力配置字段。")


class Capabilities:
    def __init__(self, settings, database):
        self.settings, self.database = settings, database

    def definition(self, payload, original=None):
        if set(payload) - {"kind", "name", "description", "config", "revision"}:
            invalid("包含不支持的能力配置字段。")
        kind = payload.get("kind", original.kind if original else None)
        if not isinstance(kind, str) or kind not in KINDS or (original and kind != original.kind):
            invalid("请选择有效的能力类型。保存后不能更改类型。")
        name = string(payload.get("name"), "名称", 128, required=True).strip()
        desc = string(payload.get("description", ""), "说明", 2000).strip()
        config = payload.get("config")
        if not isinstance(config, dict):
            invalid("能力配置应是 JSON 对象。")
        if kind == "skill":
            config_keys(config, {"instructions", "files"})
            config = {"instructions": string(config.get("instructions", ""), "Skill 指引", 128 * 1024),
                      "files": normalize_files(config.get("files", {}))}
        elif kind == "mcp":
            config_keys(config, {"url", "transport", "auth_type", "credential_variable", "allowed_tools"})
            url = string(config.get("url", ""), "MCP 服务地址", 2048).strip()
            transport = config.get("transport", "streamable_http")
            auth_type = config.get("auth_type", "none")
            if transport != "streamable_http" or auth_type not in ("none", "bearer"):
                invalid("请选择支持的 MCP 连接方式和成员认证方式。")
            if url:
                self.mcp_address(url, require_https=auth_type == "bearer")
            credential_name = string(config.get("credential_variable", ""), "凭据名称", 128).strip()
            if any(ord(char) < 32 for char in credential_name):
                invalid("凭据名称应为单行文字。")
            config = {"url": url, "transport": transport, "auth_type": auth_type,
                      "credential_variable": credential_name if auth_type == "bearer" else "",
                      "allowed_tools": string_list(config.get("allowed_tools", []), "可用工具")}
        else:
            config_keys(config, {"instructions", "default_model_id", "skill_ids", "mcp_ids"})
            model_id = string(config.get("default_model_id", ""), "默认模型", 32)
            if model_id and not IDENTIFIER.fullmatch(model_id):
                invalid("默认模型标识无效。")
            config = {"instructions": string(config.get("instructions", ""), "专家指引", 128 * 1024),
                      "default_model_id": model_id,
                      "skill_ids": string_list(config.get("skill_ids", []), "Skill", identifiers=True),
                      "mcp_ids": string_list(config.get("mcp_ids", []), "MCP", identifiers=True)}
        return kind, name, desc, config

    def mcp_address(self, url, *, require_https=False):
        try:
            address = urlsplit(url)
            port = address.port
        except ValueError:
            invalid("MCP 服务地址格式不正确。")
        if (not address.hostname or address.username is not None or address.password is not None
                or "?" in url or "#" in url or "\\" in url or any(ch.isspace() or ord(ch) < 32 for ch in url)
                or (port is not None and not 0 < port < 65536)
                or address.scheme not in (("https", "http") if self.settings.development else ("https",))):
            invalid("请输入有效的 MCP HTTPS 服务地址，地址中不要附加凭据或参数。")
        if require_https and address.scheme != "https":
            invalid("使用 Bearer 凭据时，MCP 服务必须使用 HTTPS。")

    @staticmethod
    def dependencies(config):
        return [(kind, key) for kind in ("skill", "mcp") for key in config.get(f"{kind}_ids", [])]

    def validate(self, db, kind, config):
        if kind in {"skill", "expert"} and not config["instructions"].strip():
            invalid("请填写工作指引。")
        if kind == "skill":
            for path, content in config["files"].items():
                if path.startswith("scripts/") and path.endswith(".py"):
                    try:
                        ast.parse(content, filename=path)
                    except (SyntaxError, ValueError):
                        invalid(f"脚本 {path} 的 Python 语法无效。")
        if kind == "mcp":
            self.mcp_address(config["url"], require_https=config["auth_type"] == "bearer")
            if config["auth_type"] == "bearer" and not config["credential_variable"]:
                invalid("请为员工需要填写的访问令牌提供凭据名称。")
        if kind == "expert":
            for dep_kind, key in self.dependencies(config):
                dependency = db.get(Capability, key)
                if not dependency or dependency.kind != dep_kind or not dependency.enabled or not dependency.current_version:
                    invalid("专家关联的 Skill 或 MCP 尚未发布，或已停用。")
            model_id = config["default_model_id"]
            model = db.get(Model, model_id) if model_id else None
            if model_id and (model is None or not model.enabled):
                invalid("请选择已启用的默认模型，或留空由员工选择。")

    @staticmethod
    def find(db, key):
        row = db.get(Capability, key) if IDENTIFIER.fullmatch(key) else None
        if not row:
            raise PlatformError("capability_not_found", "此能力不存在，请返回列表。", 404)
        return row

    @staticmethod
    def view_admin(row, *, configuration=True):
        result = {"id": row.id, "kind": row.kind, "name": row.draft_name,
                  "description": row.draft_description, "revision": row.revision, "version": row.current_version,
                  "enabled": row.enabled, "tested": row.draft_tested,
                  "all_members": row.all_members, "member_ids": row.member_ids, "department_ids": row.department_ids}
        if configuration:
            result["config"] = row.draft_config
        return result

    def admin_list(self):
        with self.database.transaction() as db:
            rows = db.execute(select(Capability, CapabilityVersion.name)
                .options(defer(Capability.draft_config))
                .outerjoin(CapabilityVersion, (CapabilityVersion.capability_id == Capability.id)
                           & (CapabilityVersion.version == Capability.current_version))
                .order_by(Capability.kind, Capability.draft_name, Capability.id))
            return {"items": [{**self.view_admin(row, configuration=False), "published_name": name or ""}
                              for row, name in rows]}

    def admin_detail(self, key):
        with self.database.transaction() as db:
            row = self.find(db, key)
            versions = db.execute(select(CapabilityVersion.version, CapabilityVersion.name, CapabilityVersion.published_at)
                .where(CapabilityVersion.capability_id == key).order_by(CapabilityVersion.version.desc()))
            current = db.get(CapabilityVersion, (key, row.current_version)) if row.current_version else None
            draft_pending = not current or self.draft_differs(row, current)
            return {**self.view_admin(row), "draft_pending": draft_pending,
                    "versions": [{"version": item.version, "name": item.name,
                      "published_at": item.published_at} for item in versions]}

    @staticmethod
    def draft_differs(row, release):
        return (row.draft_name, row.draft_description, row.draft_config) != (release.name, release.description, release.config)

    @staticmethod
    def revise(db, row, revision, **values):
        if type(revision) is not int or revision != row.revision:
            raise PlatformError("capability_conflict", "能力已被更新。请打开最新版本，再合并你的修改。", 409)
        result = db.execute(update(Capability).where(Capability.id == row.id, Capability.revision == revision)
                            .values(revision=revision + 1, **values))
        if result.rowcount != 1:
            raise PlatformError("capability_conflict", "能力已被更新。请打开最新版本，再合并你的修改。", 409)
        db.refresh(row)

    def save(self, actor, payload, key=None):
        with self.database.transaction() as db:
            row = self.find(db, key) if key else None
            kind, name, description, config = self.definition(payload, row)
            values = {"draft_name": name, "draft_description": description, "draft_config": config}
            if row is None:
                row = Capability(kind=kind, **values)
                db.add(row)
                db.flush()
            else:
                self.revise(db, row, payload.get("revision"), draft_tested=False, **values)
            self.database.audit(db, "capability.draft_saved", member_id=actor, target=row.id)
            result = self.view_admin(row)
        log.info("capability.draft_saved id=%s actor=%s revision=%s", result["id"], actor, result["revision"])
        return result

    def import_package(self, actor, kind, data, key=None, revision=None):
        package = read_capability_package(kind, data)
        with self.database.transaction() as db:
            row = self.find(db, key) if key else None
            if row and row.revision != revision:
                raise PlatformError("capability_conflict", "此草稿已被更新。请打开最新版本后再上传 ZIP。", 409)
            kind, name, description, config = self.definition(package.definition, row)
            capability_id = row.id if row else uid()
            bundled_skills = []
            for folder, definition in package.bundled_skills.items():
                skill_id = hashlib.sha256(f"expert-skill\0{capability_id}\0{folder}".encode()).hexdigest()[:32]
                skill = db.get(Capability, skill_id)
                _, skill_name, skill_description, skill_config = self.definition(definition, skill)
                values = {"draft_name": skill_name, "draft_description": skill_description, "draft_config": skill_config}
                if skill is None:
                    skill = Capability(id=skill_id, kind="skill", **values)
                    db.add(skill)
                    db.flush()
                elif any(getattr(skill, field) != value for field, value in values.items()):
                    self.revise(db, skill, skill.revision, draft_tested=False, **values)
                self.database.audit(db, "capability.zip_imported", member_id=actor, target=skill.id)
                bundled_skills.append({"id": skill_id, "name": skill_name, "version": skill.current_version})
            if bundled_skills:
                config["skill_ids"] = sorted(set(config["skill_ids"]) | {skill["id"] for skill in bundled_skills})
            values = {"draft_name": name, "draft_description": description, "draft_config": config}
            if row is None:
                row = Capability(id=capability_id, kind=kind, **values)
                db.add(row)
                db.flush()
            else:
                self.revise(db, row, revision, draft_tested=False, **values)
            self.database.audit(db, "capability.zip_imported", member_id=actor, target=row.id)
            result = {**self.view_admin(row, configuration=False), "bundled_skills": bundled_skills}
        log.info("capability.zip_imported id=%s actor=%s revision=%s bundled_skills=%s",
                 result["id"], actor, result["revision"], len(bundled_skills))
        return result

    def admin_action(self, actor, key, action, payload):
        if action not in {"test", "publish", "disable", "grant"}:
            raise PlatformError("unknown_capability_action", "此能力操作不可用。", 404)
        if set(payload) - ({"revision", "all_members", "member_ids", "department_ids"} if action == "grant" else {"revision"}):
            invalid("包含不支持的能力操作字段。")
        revision = payload.get("revision")
        with self.database.transaction() as db:
            row = self.find(db, key)
            if type(revision) is not int or revision != row.revision:
                raise PlatformError("capability_conflict", "能力已被更新。请打开最新版本，再合并你的修改。", 409)
            if action == "test":
                self.validate(db, row.kind, row.draft_config)
                self.revise(db, row, revision, draft_tested=True)
            elif action == "publish":
                self.publish(db, row, revision)
            elif action == "disable":
                if not row.enabled:
                    invalid("此能力尚未发布或已经停用。")
                self.revise(db, row, revision, enabled=False)
            else:
                self.save_grants(db, row, revision, payload)
            self.database.audit(db, f"capability.{action}", member_id=actor, target=row.id)
            result = self.view_admin(row)
        log.info("capability.%s id=%s actor=%s revision=%s version=%s",
                 action, key, actor, result["revision"], result["version"])
        return result

    def publish(self, db, row, revision):
        if not row.draft_tested:
            invalid("请先测试已保存的草稿，再发布。")
        self.validate(db, row.kind, row.draft_config)
        release = db.get(CapabilityVersion, (row.id, row.current_version)) if row.current_version else None
        if release and not self.draft_differs(row, release):
            if row.enabled:
                invalid("当前草稿已经发布。请先修改草稿，再发布新版本。")
            self.revise(db, row, revision, enabled=True)
            return
        version = row.current_version + 1
        self.revise(db, row, revision, current_version=version, enabled=True)
        db.add(CapabilityVersion(capability_id=row.id, version=version, name=row.draft_name,
                                 description=row.draft_description, config=row.draft_config))

    def save_grants(self, db, row, revision, payload):
        everyone = payload.get("all_members")
        if type(everyone) is not bool:
            invalid("请选择授权范围。")
        member_ids = string_list(payload.get("member_ids", []), "授权成员", size=10000, identifiers=True)
        units = checked_units(db, payload.get("department_ids", [] if everyone else row.department_ids))
        if everyone and (member_ids or units):
            invalid("全员授权无需再选择指定成员。")
        if member_ids:
            present = set(db.scalars(select(Member.id).where(Member.id.in_(member_ids))))
            if present != set(member_ids):
                invalid("授权列表中存在无效成员，请刷新后重试。")
        self.revise(db, row, revision, all_members=everyone, member_ids=member_ids, department_ids=units)

    @staticmethod
    def granted(db, rows, member_id):
        units = ancestors(db, db.get(Member, member_id))
        return {row.id: row for row in rows if row.enabled and row.current_version
                and (row.all_members or member_id in row.member_ids or units.intersection(row.department_ids))}

    @staticmethod
    def verify_member(db, member_id):
        member = db.get(Member, member_id)
        if not member or not member.enabled:
            raise PlatformError("member_not_allowed", "企业账号当前不可用。", 403)

    def list_for_member(self, member_id):
        with self.database.transaction() as db:
            self.verify_member(db, member_id)
            released = db.execute(select(Capability, CapabilityVersion)
                .options(defer(Capability.draft_config))
                .join(CapabilityVersion, (CapabilityVersion.capability_id == Capability.id)
                      & (CapabilityVersion.version == Capability.current_version))
                .where(Capability.enabled)
                .order_by(Capability.kind, CapabilityVersion.name, Capability.id)).all()
            allowed = self.granted(db, (row for row, version in released), member_id)
            items = []
            for row, release in released:
                if row.id not in allowed:
                    continue
                if row.kind == "expert" and not self.dependencies_granted(release.config, allowed):
                    items.append({**self.release_view(row, release), "config": {}, "available": False,
                                  "reason": "该专家所需的 Skill 或 MCP 已停用，或你尚未获得授权。"})
                else:
                    items.append(self.release_view(row, release))
            return {"items": items}

    def dependencies_granted(self, config, allowed):
        return all((row := allowed.get(key)) and row.kind == kind for kind, key in self.dependencies(config))

    def check(self, member_id, payload):
        items = payload.get("items")
        include_config = payload.get("include_config", False)
        if (set(payload) - {"items", "include_config"} or type(include_config) is not bool
                or not isinstance(items, list) or not 0 < len(items) <= 250):
            invalid("请选择要核对的企业能力。")
        for ref in items:
            if (not isinstance(ref, dict) or set(ref) != {"id", "kind", "version"}
                    or not isinstance(ref["id"], str) or not IDENTIFIER.fullmatch(ref["id"])
                    or ref["kind"] not in ("skill", "mcp", "expert")
                    or type(ref["version"]) is not int or not 0 < ref["version"] <= 2_147_483_647):
                invalid("企业能力标识或版本无效。")
        with self.database.transaction() as db:
            self.verify_member(db, member_id)
            ids = {ref["id"] for ref in items}
            requested = {(ref["id"], ref["version"]) for ref in items}
            versions = db.scalars(select(CapabilityVersion).where(
                tuple_(CapabilityVersion.capability_id, CapabilityVersion.version).in_(requested)))
            by_version = {(item.capability_id, item.version): item for item in versions}
            for version in by_version.values():
                ids.update(key for _, key in self.dependencies(version.config))
            allowed = self.granted(db, db.scalars(select(Capability).options(defer(Capability.draft_config))
                                   .where(Capability.id.in_(ids))), member_id)
            for ref in items:
                row = allowed.get(ref["id"])
                version = by_version.get((ref["id"], ref["version"]))
                if (not row or row.kind != ref["kind"] or not version
                        or (row.kind == "expert" and not self.dependencies_granted(version.config, allowed))):
                    raise PlatformError("capability_not_allowed", "企业能力已停用、权限已变更或缺少关联能力授权，请刷新后再试。", 403)
            result = {"allowed": True}
            if include_config:
                result["items"] = [
                    self.release_view(allowed[key], by_version[key, version]) for key, version in sorted(requested)]
            return result

    @staticmethod
    def release_view(row, release):
        return {"id": row.id, "kind": row.kind, "name": release.name, "description": release.description,
                "version": release.version, "config": release.config}


def capability_router(service, principal, read_json):
    router = APIRouter(prefix="/api/v1")

    async def read_zip(request):
        mime = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if mime not in {"application/zip", "application/octet-stream", "application/x-zip-compressed"}:
            invalid_package("请直接上传 ZIP 文件。", 415)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_ZIP_BYTES:
                invalid_package("请选择不超过 4 MiB 的 ZIP 文件。", 413)
            data.extend(chunk)
        return bytes(data)

    @router.get("/capabilities")
    def member_catalog(request: Request):
        return service.list_for_member(principal(request)["id"])

    @router.post("/capabilities/check")
    async def check(request: Request):
        actor = principal(request)["id"]
        return await run_in_threadpool(service.check, actor, await read_json(request, 64 * 1024))

    @router.get("/admin/capabilities")
    def admin_catalog(request: Request):
        principal(request, admin=True)
        return service.admin_list()

    @router.get("/admin/capabilities/zip-template/{kind}")
    def package_template(kind: str, request: Request):
        principal(request, admin=True)
        data = capability_zip_template(kind)
        return Response(data, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="cowork-{kind}-template.zip"'})

    @router.post("/admin/capabilities/zip/{kind}", status_code=201)
    async def upload_package(kind: str, request: Request):
        actor = principal(request, admin=True)["id"]
        return await run_in_threadpool(service.import_package, actor, kind, await read_zip(request))

    @router.put("/admin/capabilities/zip/{kind}/{key}")
    async def update_package(kind: str, key: str, revision: int, request: Request):
        actor = principal(request, admin=True)["id"]
        return await run_in_threadpool(service.import_package, actor, kind, await read_zip(request), key, revision)

    @router.get("/admin/capabilities/{key}")
    def admin_detail(key: str, request: Request):
        principal(request, admin=True)
        return service.admin_detail(key)

    @router.post("/admin/capabilities", status_code=201)
    async def create(request: Request):
        actor = principal(request, admin=True)["id"]
        return await run_in_threadpool(service.save, actor, await read_json(request, MAX_CONFIG_BYTES))

    @router.put("/admin/capabilities/{key}")
    async def save(key: str, request: Request):
        actor = principal(request, admin=True)["id"]
        return await run_in_threadpool(service.save, actor, await read_json(request, MAX_CONFIG_BYTES), key)

    @router.post("/admin/capabilities/{key}/{action}")
    async def admin_action(key: str, action: str, request: Request):
        actor = principal(request, admin=True)["id"]
        payload = await read_json(request, 512 * 1024)
        try:
            return await run_in_threadpool(service.admin_action, actor, key, action, payload)
        except PlatformError as exc:
            log.warning("capability.action_failed id=%s actor=%s code=%s",
                        key if IDENTIFIER.fullmatch(key) else "", actor, exc.code)
            raise

    return router
