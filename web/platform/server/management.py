"""Member authorization and model configuration."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

from sqlalchemy import delete, func, select

from .config import service_url
from .database import DatabaseAccount, Department, LocalAdministrator, LoginSession, Member, Model, ModelGrant, ModelUsageDay, Platform
from .departments import ancestors, available_models, checked_units, model_allowed
from .security import PlatformError, digest, encode, equal

MODEL_OPTIONS = {"supports_vision", "supports_image_generation", "stream_usage_enabled", "deepseek_thinking_enabled",
                 "reasoning_effort", "reasoning_efforts", "prompt_cache_key_param", "context_window_tokens",
                 "max_output_tokens", "anthropic_auth"}


def configured_bootstrap_admin(db, settings):
    provider, subject = settings.bootstrap_provider, settings.bootstrap_subject
    if not provider or not subject or provider == "__platform_admin__":
        return None
    return db.scalar(select(Member).where(Member.provider_id == provider, Member.subject == subject, Member.role == "admin"))


def super_admin_id(db, settings, local_admin):
    if local_admin is not None:
        member = db.get(Member, local_admin.member_id)
        if not member or member.provider_id != "__platform_admin__":
            return None
    else:
        member = configured_bootstrap_admin(db, settings)
    return member.id if member and member.role == "admin" and member.enabled else None


def member_view(member):
    return {"id": member.id, "name": member.name, "email": member.email, "provider_id": member.provider_id,
            "role": member.role, "enabled": member.enabled, "revision": member.revision, "department_id": member.department_id,
            "default_model_id": member.default_model_id, "created_at": member.created_at, "last_login_at": member.last_login_at}


def model_view(model, *, admin=False):
    result = {"id": model.id, "name": model.name, "provider": model.provider, "protocol": model.protocol,
              "model_name": model.model_name, "options": model.options, "base_url": model.base_url, "revision": model.revision}
    if admin:
        result.update(base_url=model.base_url, enabled=model.enabled, revision=model.revision, department_ids=model.department_ids,
                      tested=model.tested_revision == model.revision, has_key=bool(model.secret))
    return result


class Management:
    def __init__(self, settings, database, vault):
        self.settings, self.database, self.vault = settings, database, vault

    def principal(self, raw_token, *, cookie=False, csrf=None, writing=False):
        if not raw_token:
            raise PlatformError("authentication_required", "请先登录企业平台。", 401)
        with self.database.transaction() as db:
            session = db.get(LoginSession, digest(raw_token))
            if not session or session.expires_at <= time.time() or (cookie and session.kind != "web"):
                raise PlatformError("authentication_required", "登录已失效，请重新登录。", 401)
            if cookie and writing and (not csrf or not equal(session.csrf_hash, digest(csrf))):
                raise PlatformError("csrf_failed", "页面校验已失效，请刷新后重试。", 403)
            member = db.get(Member, session.member_id)
            if not member or not member.enabled:
                raise PlatformError("member_disabled", "此成员已停用，请联系管理员。", 403)
            if session.kind == "web" and member.role != "admin":
                raise PlatformError("authentication_required", "网页版管理员会话已失效，请重新登录。", 401)
            return member_view(member)

    @staticmethod
    def administrator(principal):
        if principal["role"] != "admin":
            raise PlatformError("forbidden", "此操作需要平台管理员权限。", 403)
        return principal

    def members(self):
        with self.database.transaction() as db:
            grants = list(db.scalars(select(ModelGrant)))
            usernames = dict(db.execute(select(DatabaseAccount.member_id, DatabaseAccount.username)).all())
            founder = db.get(LocalAdministrator, 1)
            super_id = super_admin_id(db, self.settings, founder)
            parents = dict(db.execute(select(Department.id, Department.parent_id)).all())
            inherited = {}
            models = list(db.scalars(select(Model)))
            for unit in parents:
                path, current = set(), unit
                while current:
                    path.add(current)
                    current = parents[current]
                inherited[unit] = [m.id for m in models if path.intersection(m.department_ids)]
            return [{**member_view(member), "username": usernames.get(member.id, ""),
                     "inherited_model_ids": inherited.get(member.department_id, []),
                     "is_super_admin": member.id == super_id,
                     "model_ids": [g.model_id for g in grants if g.member_id == member.id]}
                    for member in db.scalars(select(Member).order_by(Member.created_at))]

    def count_model_request(self):
        # Track upstream-accepted employee requests independently of retained chat content.
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        insert = pg_insert if self.database.engine.dialect.name == "postgresql" else sqlite_insert
        with self.database.transaction() as db:
            db.execute(insert(ModelUsageDay).values(day=day, requests=1).on_conflict_do_update(
                index_elements=[ModelUsageDay.day], set_={"requests": ModelUsageDay.requests + 1}))

    def model_usage_week(self):
        today = datetime.now(timezone(timedelta(hours=8))).date()
        days = [(today - timedelta(days=i)).isoformat() for i in reversed(range(7))]
        with self.database.transaction() as db:
            counts = dict(db.execute(select(ModelUsageDay.day, ModelUsageDay.requests)
                                     .where(ModelUsageDay.day.in_(days))).all())
        return [{"day": day, "requests": counts.get(day, 0)} for day in days]

    def departments(self):
        with self.database.transaction() as db:
            return [{"id": d.id, "name": d.name, "parent_id": d.parent_id}
                    for d in db.scalars(select(Department).order_by(Department.name))]

    def create_department(self, payload, actor):
        name, parent = payload.get("name"), payload.get("parent_id", "")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or not isinstance(parent, str):
            raise PlatformError("invalid_department", "请填写部门名称并选择上级部门。")
        with self.database.transaction() as db:
            db.scalar(select(Platform).where(Platform.id == 1).with_for_update())
            checked_units(db, [parent] if parent else [])
            if db.scalar(select(Department.id).where(Department.parent_id == parent, Department.name == name.strip())):
                raise PlatformError("department_exists", "上级部门下已有同名部门。", 409)
            unit = Department(name=name.strip(), parent_id=parent)
            db.add(unit)
            db.flush()
            self.database.audit(db, "department.created", member_id=actor, target=unit.id)
            return {"id": unit.id, "name": unit.name, "parent_id": parent}

    def update_member(self, member_id, payload, actor):
        with self.database.transaction() as db:
            # All member mutations share this lock, including the last-admin check.
            from .database import Platform
            db.scalar(select(Platform).where(Platform.id == 1).with_for_update())
            member = db.get(Member, member_id)
            if not member:
                raise PlatformError("not_found", "成员不存在。", 404)
            if payload.get("revision") != member.revision:
                raise PlatformError("conflict", "成员配置已变化，请刷新核对。", 409)
            role = payload.get("role", member.role)
            enabled = payload.get("enabled", member.enabled)
            if role not in {"admin", "member"} or type(enabled) is not bool:
                raise PlatformError("invalid_member", "成员角色或启用状态无效。")
            founder = db.get(LocalAdministrator, 1)
            super_id = super_admin_id(db, self.settings, founder)
            reserved_id = founder.member_id if founder else super_id
            if member.id == reserved_id and (role != "admin" or not enabled):
                raise PlatformError("founder_required", "超级管理员负责管理员授权，不能停用或降级。", 409)
            if role != member.role:
                if super_id is None:
                    raise PlatformError("super_admin_unavailable", "部署未配置可用的超级管理员，请联系部署维护人员。", 403)
                if actor != super_id:
                    raise PlatformError("founder_required", "管理员角色只能由超级管理员授权或收回。", 403)
            if member.role == "admin" and member.enabled and (role != "admin" or not enabled):
                admins = db.scalar(select(func.count()).select_from(Member).where(Member.role == "admin", Member.enabled))
                if admins <= 1:
                    raise PlatformError("last_admin", "不能停用或降级最后一位平台管理员。", 409)
            if "department_id" in payload:
                unit = payload["department_id"]
                if not isinstance(unit, str) or member.provider_id == "__platform_admin__":
                    raise PlatformError("invalid_department", "请选择有效的员工与部门。")
                checked_units(db, [unit] if unit else [])
                member.department_id = unit
            if "model_ids" in payload:
                model_ids = payload["model_ids"]
                if not isinstance(model_ids, list) or len(model_ids) > 200 or any(not isinstance(x, str) for x in model_ids):
                    raise PlatformError("invalid_models", "模型授权列表无效。")
                available = set(db.scalars(select(Model.id).where(Model.id.in_(model_ids))))
                if set(model_ids) != available:
                    raise PlatformError("unknown_model", "授权列表包含不存在的模型。")
                default_id = payload.get("default_model_id", "")
                units = ancestors(db, member)
                effective_ids = set(model_ids) | {m.id for m in db.scalars(select(Model)) if units.intersection(m.department_ids)}
                if (effective_ids and default_id not in effective_ids) or (not effective_ids and default_id):
                    raise PlatformError("invalid_default", "默认模型必须属于该成员的授权列表。")
                db.execute(delete(ModelGrant).where(ModelGrant.member_id == member_id))
                db.add_all(ModelGrant(member_id=member_id, model_id=x) for x in sorted(set(model_ids)))
                member.default_model_id = default_id
            role_changed = role != member.role
            member.role, member.enabled = role, enabled
            member.revision += 1
            if not enabled:
                db.execute(delete(LoginSession).where(LoginSession.member_id == member_id))
            elif role_changed:
                db.execute(delete(LoginSession).where(LoginSession.member_id == member_id, LoginSession.kind == "web"))
            self.database.audit(db, "member.updated", member_id=actor, target=member_id)
            return member_view(member)

    def save_model(self, payload, actor, model_id=None):
        required = {"name", "provider", "protocol", "model_name", "base_url"}
        if not required <= set(payload):
            raise PlatformError("invalid_model", "请填写模型名称、协议、标识和服务地址。")
        if payload["provider"] not in {"openai", "moonshot", "anthropic"}:
            raise PlatformError("invalid_model", "当前只支持 OpenAI 兼容、Moonshot 和 Anthropic。")
        protocols = {"anthropic"} if payload["provider"] == "anthropic" else {"chat_completions", "responses"}
        if payload["protocol"] not in protocols:
            raise PlatformError("invalid_model", "模型服务与 API 协议不匹配。")
        for key, length in (("name", 128), ("model_name", 256)):
            if not isinstance(payload[key], str) or not 1 <= len(payload[key].strip()) <= length:
                raise PlatformError("invalid_model", "模型字段无效：" + key)
        options = payload.get("options", {})
        if not isinstance(options, dict) or set(options) - MODEL_OPTIONS or len(encode(options)) > 10000:
            raise PlatformError("invalid_options", "模型选项包含不支持的字段。")
        if options.get("anthropic_auth", "api_key") not in {"api_key", "bearer"}:
            raise PlatformError("invalid_options", "Anthropic 认证方式无效。")
        address = service_url(payload["base_url"], allow_local_http=self.settings.development, allow_http=self.settings.allow_model_http)
        with self.database.transaction() as db:
            model = db.get(Model, model_id) if model_id else None
            if model_id and model is None:
                raise PlatformError("not_found", "模型不存在。", 404)
            if model and payload.get("revision") != model.revision:
                raise PlatformError("conflict", "模型配置已变化，请刷新核对。", 409)
            old_key = self.vault.open(model.secret)["api_key"] if model else ""
            key = payload.get("api_key") or old_key
            if not isinstance(key, str) or not key.strip() or len(key) > 10000:
                raise PlatformError("key_required", "请填写模型服务密钥。")
            fields = dict(name=payload["name"].strip(), provider=payload["provider"], protocol=payload["protocol"],
                          model_name=payload["model_name"].strip(), base_url=address, options=options)
            if model is None:
                model = Model(revision=1, tested_revision=0)
                db.add(model)
            elif old_key != key or any(getattr(model, k) != v for k, v in fields.items()):
                model.revision += 1
            for key_name, value in fields.items():
                setattr(model, key_name, value)
            model.secret = self.vault.seal({"api_key": key})
            model.enabled = bool(payload.get("enabled"))
            if model.enabled and model.tested_revision != model.revision:
                raise PlatformError("test_required", "请先保存并测试实际调用，再启用模型。")
            db.flush()
            self.database.audit(db, "model.saved", member_id=actor, target=model.id)
            return model_view(model, admin=True)

    def models(self, member_id=None):
        with self.database.transaction() as db:
            query = select(Model).order_by(Model.name)
            if member_id:
                return [model_view(m) for m in available_models(db, member_id, enabled_only=True)]
            return [model_view(m, admin=not member_id) for m in db.scalars(query)]

    def update_model_grants(self, model_id, payload, actor):
        from .database import Platform

        requested, baseline = (payload.get(key) for key in ("member_ids", "previous_member_ids"))
        for values in (requested, baseline):
            if not isinstance(values, list) or any(not isinstance(value, str) or not 1 <= len(value) <= 128 for value in values):
                raise PlatformError("invalid_members", "请选择有效的员工。")
        desired = set(requested)
        with self.database.transaction() as db:
            # Share the lock with member-side model authorization.
            db.scalar(select(Platform).where(Platform.id == 1).with_for_update())
            operator = db.get(Member, actor)
            if not operator or not operator.enabled or operator.role != "admin":
                raise PlatformError("forbidden", "此操作需要平台管理员权限。", 403)
            model = db.get(Model, model_id)
            if not model:
                raise PlatformError("not_found", "模型不存在。", 404)
            founder = db.get(LocalAdministrator, 1)
            if founder and founder.member_id in desired:
                raise PlatformError("invalid_members", "此账号不使用员工模型。")
            current = set(db.scalars(select(ModelGrant.member_id).where(ModelGrant.model_id == model_id)))
            if founder:
                current.discard(founder.member_id)
            change_units = False
            if "department_ids" in payload:
                units = checked_units(db, payload["department_ids"])
                change_units = units != sorted(model.department_ids)
                if change_units and payload.get("previous_department_ids") != sorted(model.department_ids):
                    raise PlatformError("conflict", "部门授权已变化，请刷新核对。", 409)
                model.department_ids = units
            if current == desired and not change_units:
                return {"member_ids": sorted(current)}
            if current != set(baseline):
                raise PlatformError("conflict", "模型授权已变化，请刷新后核对员工。", 409)

            changes = current ^ desired
            members = {member.id: member for member in db.scalars(select(Member).where(Member.id.in_(changes)))}
            if members.keys() != changes:
                raise PlatformError("unknown_member", "员工列表已变化，请刷新后核对。", 409)
            grants = {member_id: set() for member_id in changes}
            for member_id, existing_model in db.execute(
                select(ModelGrant.member_id, ModelGrant.model_id).where(ModelGrant.member_id.in_(changes))
            ):
                grants[member_id].add(existing_model)
            enabled_models = set(db.scalars(select(Model.id).where(Model.enabled)))
            for member_id, member in members.items():
                if member_id in desired:
                    if len(grants[member_id]) >= 200:
                        raise PlatformError("invalid_models", f"{member.name}已达到可分配模型数量上限。")
                    db.add(ModelGrant(member_id=member_id, model_id=model_id))
                    if not member.default_model_id:
                        member.default_model_id = model_id
                else:
                    db.delete(db.get(ModelGrant, (member_id, model_id)))
                    if member.default_model_id == model_id:
                        remaining = grants[member_id] - {model_id}
                        member.default_model_id = min(remaining, key=lambda key: (key not in enabled_models, key), default="")
                member.revision += 1
            self.database.audit(db, "model.grants_changed", member_id=actor, target=model_id)
        return {"member_ids": sorted(desired)}

    def authorized_model(self, member_id, model_id):
        with self.database.transaction() as db:
            member, model = db.get(Member, member_id), db.get(Model, model_id)
            if not model_allowed(db, member, model):
                raise PlatformError("model_forbidden", "当前账号未获此模型授权，或模型已停用。", 403)
            return {**model_view(model, admin=True), "api_key": self.vault.open(model.secret)["api_key"]}
