"""Team collaboration transactions. Private conversations never enter this service."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import logging
import os
import re
import time

from sqlalchemy import func, select, update

from .database import LocalAdministrator, Member, Model, ModelGrant, Platform, uid
from .security import PlatformError, digest, encode
from .team_space_tables import (TeamSpace, SpaceMember, SpaceInvite, SpacePlan, SpaceComment,
    SpaceActivity, SpaceFolder, SpaceUpload, SpaceAsset, SpaceAssetRevision, SpaceShare,
    SpaceSnapshot, SpaceOperation)

log = logging.getLogger(__name__)
OBJECTS = {"plan": SpacePlan, "comment": SpaceComment, "asset": SpaceAsset, "share": SpaceShare,
           "folder": SpaceFolder, "upload": SpaceUpload, "snapshot": SpaceSnapshot,
           "asset_revision": SpaceAssetRevision, "invite": SpaceInvite}
COLLECTIONS = {"plans": SpacePlan, "comments": SpaceComment, "assets": SpaceAsset,
               "shares": SpaceShare, "folders": SpaceFolder, "activity": SpaceActivity, "invites": SpaceInvite}


def text(value, maximum, *, required=False):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise PlatformError("invalid_value", "文本格式或长度不符合要求。")
    value = value.strip()
    if required and not value:
        raise PlatformError("value_required", "请填写必填内容。")
    return value


def name(value):
    value = text(value, 200, required=True)
    if value in {".", ".."} or any(c in value for c in '\\/:*?"<>|') or any(ord(c) < 32 for c in value):
        raise PlatformError("invalid_name", "名称不能包含路径分隔符或控制字符。")
    if value.endswith(".") or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", value.split(".")[0]):
        raise PlatformError("invalid_name", "名称不能使用 Windows 保留文件名或以句点结尾。")
    return value


def version(item, payload):
    if type(payload.get("revision")) is not int or payload["revision"] != item.revision:
        raise PlatformError("version_conflict", "内容已被其他人更新。草稿仍保留，请对照最新版本后重新提交。", 409)


def fields(payload, allowed):
    if set(payload) - set(allowed) - {"operation_id"}:
        raise PlatformError("unknown_field", "请求包含不支持的字段，请更新客户端后重试。")


class TeamSpaces:
    def __init__(self, settings, database):
        self.settings, self.database = settings, database
        self.root = settings.data_dir / "team-space-files"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextmanager
    def transaction(self):
        with self.database.transaction() as db:
            # SQLite is only a contract-test/development backend. Serialize its
            # short writes explicitly; PostgreSQL uses the locked space row.
            if self.database.engine.dialect.name == "sqlite":
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            yield db

    def employee(self, db, actor):
        actor = text(actor, 128, required=True)
        member = db.get(Member, actor)
        founder = db.get(LocalAdministrator, 1)
        if not member or not member.enabled or (founder and founder.member_id == actor):
            raise PlatformError("employee_required", "请使用有效的企业员工账号进入团队空间。", 403)
        return member

    def access(self, db, space_id, actor, *, lock=False):
        self.employee(db, actor)
        query = select(TeamSpace).where(TeamSpace.id == space_id)
        space = db.scalar(query.with_for_update() if lock else query)
        membership = db.get(SpaceMember, (space_id, actor))
        if not space or not membership or not membership.active:
            raise PlatformError("space_forbidden", "此空间不存在或你已没有访问权限。", 403)
        return space, membership

    @staticmethod
    def manager(membership, *, owner=False):
        if membership.role not in ({"owner"} if owner else {"owner", "admin"}):
            raise PlatformError("space_role_required", "此操作需要空间所有者权限。" if owner else "此操作需要空间管理权限。", 403)

    @staticmethod
    def active(space):
        if space.state != "active":
            raise PlatformError("space_archived", "空间已归档，内容保留可读；恢复空间后才能编辑或发布。", 409)

    @staticmethod
    def object(db, kind, object_id, space_id):
        cls = OBJECTS.get(kind)
        item = db.get(cls, object_id) if cls and isinstance(object_id, str) else None
        if not item or item.space_id != space_id:
            raise PlatformError("object_unavailable", "内容不存在或不属于当前空间。", 404)
        return item

    def active_member(self, db, space_id, member_id):
        self.employee(db, member_id)
        member = db.get(SpaceMember, (space_id, member_id))
        if not member or not member.active:
            raise PlatformError("member_unavailable", "请选择仍在空间中的有效成员。", 409)
        return member

    def space_view(self, space, membership=None):
        return {"id": space.id, "name": space.name, "description": space.description,
                "owner_id": space.owner_id, "state": space.state, "revision": space.revision,
                "description_revision": space.description_revision, "event_version": space.event_version,
                "model_mode": space.model_mode, "model_ids": space.model_ids,
                "allow_personal": space.allow_personal, "skills": space.skills,
                "role": membership.role if membership else "", "updated_at": space.updated_at,
                "created_at": space.created_at}

    @staticmethod
    def member_name(db, member_id):
        member = db.get(Member, member_id)
        return member.name if member else "原成员"

    def view(self, db, item):
        result = {c.name: getattr(item, c.name) for c in item.__table__.columns}
        if isinstance(item, SpaceUpload):
            result.pop("generation", None)
        if isinstance(item, SpaceAssetRevision):
            result.pop("upload_id", None)
        if isinstance(item, SpaceComment) and item.state != "active":
            result["body"] = ""
        if isinstance(item, SpaceInvite):
            result["state"] = "expired" if item.state == "pending" and item.expires_at <= time.time() else item.state
            result["recipient_name"] = self.member_name(db, item.recipient_id)
            result["sender_name"] = self.member_name(db, item.sender_id)
        if isinstance(item, SpacePlan):
            member = db.get(Member, item.assignee_id) if item.assignee_id else None
            relation = db.get(SpaceMember, (item.space_id, item.assignee_id)) if member else None
            result["assignee_name"] = member.name if member else "未分配"
            result["assignee_available"] = bool(member and member.enabled and relation and relation.active)
        for key in ("author_id", "actor_id", "updated_by"):
            if result.get(key):
                result[key.removesuffix("_id") + "_name"] = self.member_name(db, result[key])
        return result

    def event(self, db, space, actor, kind, target_kind, target_id, details=None):
        space.event_version += 1
        space.updated_at = time.time()
        db.add(SpaceActivity(space_id=space.id, sequence=space.event_version, actor_id=actor,
            kind=kind, target_kind=target_kind, target_id=target_id, details=details or {}))
        self.database.audit(db, "space." + kind, member_id=actor, target=space.id + ":" + target_id)

    def list(self, actor, *, search="", archived=False, offset=0, limit=50):
        with self.database.transaction() as db:
            self.employee(db, actor)
            query = select(TeamSpace, SpaceMember).join(SpaceMember, SpaceMember.space_id == TeamSpace.id).where(
                SpaceMember.member_id == actor, SpaceMember.active, TeamSpace.state == ("archived" if archived else "active"))
            if search:
                query = query.where(TeamSpace.name.contains(text(search, 200), autoescape=True))
            rows = list(db.execute(query.order_by(TeamSpace.updated_at.desc(), TeamSpace.id).offset(offset).limit(limit + 1)))
            return {"items": [self.space_view(s, m) for s, m in rows[:limit]], "has_more": len(rows) > limit}

    def detail(self, actor, space_id):
        with self.database.transaction() as db:
            space, membership = self.access(db, space_id, actor)
            result = self.space_view(space, membership)
            result["members"] = [{"id": m.member_id, "name": user.name, "role": m.role,
                "active": m.active, "enabled": user.enabled, "revision": m.revision}
                for m, user in db.execute(select(SpaceMember, Member).join(Member, Member.id == SpaceMember.member_id)
                    .where(SpaceMember.space_id == space_id).order_by(SpaceMember.joined_at))]
            result["limits"] = {"file_bytes": self.settings.max_space_file_bytes, "space_bytes": self.settings.max_space_bytes}
            result["unfinished_plans"] = db.scalar(select(func.count()).select_from(SpacePlan).where(
                SpacePlan.space_id == space_id, SpacePlan.state != "completed"))
            result["models"] = [{"id": m.id, "name": m.name, "enabled": m.enabled} for m in db.scalars(
                select(Model).join(ModelGrant, ModelGrant.model_id == Model.id).where(ModelGrant.member_id == actor))]
            return result

    def candidates(self, actor, query):
        query = text(query, 200)
        with self.database.transaction() as db:
            self.employee(db, actor)
            founder = db.get(LocalAdministrator, 1)
            statement = select(Member).where(Member.enabled)
            if founder:
                statement = statement.where(Member.id != founder.member_id)
            if query:
                statement = statement.where(Member.name.contains(query, autoescape=True) | Member.email.contains(query, autoescape=True))
            return {"items": [{"id": m.id, "name": m.name, "account": m.email or m.subject}
                for m in db.scalars(statement.order_by(Member.name, Member.id).limit(50))]}

    def inbox(self, actor):
        with self.database.transaction() as db:
            self.employee(db, actor)
            return {"items": [{"id": i.id, "space_id": i.space_id, "space_name": s.name,
                "sender_name": self.member_name(db, i.sender_id), "role": i.role,
                "state": "expired" if i.state == "pending" and i.expires_at <= time.time() else i.state,
                "revision": i.revision, "expires_at": i.expires_at}
                for i, s in db.execute(select(SpaceInvite, TeamSpace).join(TeamSpace, TeamSpace.id == SpaceInvite.space_id)
                    .where(SpaceInvite.recipient_id == actor, SpaceInvite.state == "pending")
                    .order_by(SpaceInvite.created_at.desc()).limit(100))]}

    def collection(self, actor, space_id, collection, *, offset=0, limit=50, search="", after=0,
                   target_kind="", target_id="", assignee_id="", state="", folder_id=None):
        cls = COLLECTIONS.get(collection)
        if cls is None:
            raise PlatformError("not_found", "没有此内容列表。", 404)
        with self.database.transaction() as db:
            space, membership = self.access(db, space_id, actor)
            if cls is SpaceInvite:
                self.manager(membership)
            query = select(cls).where(cls.space_id == space_id)
            if search and hasattr(cls, "name"):
                query = query.where(cls.name.contains(text(search, 200), autoescape=True))
            elif search and hasattr(cls, "title"):
                query = query.where(cls.title.contains(text(search, 200), autoescape=True))
            if cls is SpaceActivity:
                query = query.where(cls.sequence > after).order_by(cls.sequence.desc())
            elif cls is SpaceComment:
                if target_kind:
                    self.target(db, space_id, target_kind, target_id)
                    query = query.where(cls.target_kind == target_kind, cls.target_id == target_id)
                query = query.order_by(cls.created_at.desc(), cls.id)
            else:
                if cls is SpacePlan:
                    if assignee_id:
                        query = query.where(cls.assignee_id == assignee_id)
                    if state:
                        query = query.where(cls.state == state)
                if cls in {SpaceAsset, SpaceFolder} and folder_id is not None:
                    if folder_id:
                        self.object(db, "folder", folder_id, space_id)
                    query = query.where((cls.folder_id if cls is SpaceAsset else cls.parent_id) == folder_id)
                query = query.order_by(cls.updated_at.desc(), cls.id) if hasattr(cls, "updated_at") else query.order_by(cls.id)
            rows = list(db.scalars(query.offset(offset).limit(limit + 1)))
            return {"items": [self.view(db, row) for row in rows[:limit]], "has_more": len(rows) > limit,
                    "event_version": space.event_version}

    def get(self, actor, space_id, kind, object_id):
        with self.database.transaction() as db:
            self.access(db, space_id, actor)
            item = self.object(db, kind, object_id, space_id)
            if kind == "upload" and item.author_id != actor:
                raise PlatformError("forbidden", "不能读取他人的待发布内容。", 403)
            if kind == "invite":
                self.manager(db.get(SpaceMember, (space_id, actor)))
            if kind == "snapshot":
                share = self.object(db, "share", item.share_id, space_id)
                if share.state != "active":
                    raise PlatformError("share_withdrawn", "成果已撤回，正文不再开放读取。", 410)
            result = self.view(db, item)
            if kind == "share" and item.state == "active":
                result["snapshots"] = [self.view(db, r) for r in db.scalars(select(SpaceSnapshot).where(
                    SpaceSnapshot.share_id == item.id).order_by(SpaceSnapshot.number.desc()))]
            if kind == "asset":
                result["revisions"] = [self.view(db, r) for r in db.scalars(select(SpaceAssetRevision).where(
                    SpaceAssetRevision.asset_id == item.id).order_by(SpaceAssetRevision.number.desc()))]
                result["references"] = self.asset_references(db, space_id, item.id)
            return result

    def target(self, db, space_id, kind, target_id):
        if kind == "space" and target_id == space_id:
            return
        if kind not in ("plan", "share", "asset"):
            raise PlatformError("invalid_target", "请选择当前空间中的计划、成果或资料。")
        self.object(db, kind, target_id, space_id)

    def asset_refs(self, db, space_id, values):
        if not isinstance(values, list) or len(values) > 100:
            raise PlatformError("invalid_references", "一次最多引用 100 份资料。")
        refs, seen = [], set()
        for value in values:
            if not isinstance(value, dict) or set(value) != {"asset_id", "revision_id"}:
                raise PlatformError("invalid_references", "资料引用必须包含资产和固定修订标识。")
            asset = self.object(db, "asset", value["asset_id"], space_id)
            revision = self.object(db, "asset_revision", value["revision_id"], space_id)
            if asset.state != "active" or revision.asset_id != asset.id:
                raise PlatformError("asset_unavailable", "资料已撤下或修订不匹配，请重新选择。", 409)
            if revision.id not in seen:
                refs.append({"asset_id": asset.id, "revision_id": revision.id})
                seen.add(revision.id)
        return refs

    def outcome_refs(self, db, space_id, values):
        if not isinstance(values, list) or len(values) > 100:
            raise PlatformError("invalid_references", "成果引用格式无效。")
        result = []
        for value in values:
            if not isinstance(value, dict) or set(value) != {"share_id", "snapshot_id"}:
                raise PlatformError("invalid_references", "请选择固定的成果修订。")
            share = self.object(db, "share", value["share_id"], space_id)
            snapshot = self.object(db, "snapshot", value["snapshot_id"], space_id)
            if share.state != "active" or snapshot.share_id != share.id:
                raise PlatformError("share_unavailable", "成果已撤回或版本不匹配。", 409)
            result.append({"share_id": share.id, "snapshot_id": snapshot.id})
        return result

    def asset_references(self, db, space_id, asset_id, *, exclude_share=""):
        refs = []
        for plan in db.scalars(select(SpacePlan).where(SpacePlan.space_id == space_id)):
            if any(r.get("asset_id") == asset_id for r in plan.inputs):
                refs.append({"kind": "plan", "id": plan.id, "title": plan.title})
        for snapshot, share in db.execute(select(SpaceSnapshot, SpaceShare).join(SpaceShare,
                SpaceShare.id == SpaceSnapshot.share_id).where(SpaceShare.space_id == space_id,
                SpaceShare.state == "active", SpaceShare.id != exclude_share)):
            if any(r.get("asset_id") == asset_id for r in snapshot.assets):
                ref = {"kind": "share", "id": share.id, "title": share.title}
                if ref not in refs:
                    refs.append(ref)
        return refs

    def operation(self, actor, operation_id):
        with self.database.transaction() as db:
            self.employee(db, actor)
            db.scalar(select(Member).where(Member.id == actor).with_for_update())
            item = db.get(SpaceOperation, digest(actor + ":" + text(operation_id, 128, required=True)))
            return {"state": "confirmed", "result": item.result} if item else {"state": "unknown"}

    def execute(self, actor, space_id, action, payload):
        if not isinstance(payload, dict):
            raise PlatformError("invalid_request", "请求必须是对象。")
        operation_id = text(payload.get("operation_id"), 128, required=True)
        key = digest(actor + ":" + operation_id)
        request_hash = digest(encode({"space": space_id, "action": action,
            "payload": {k: v for k, v in payload.items() if k != "operation_id"}}))
        with self.transaction() as db:
            self.employee(db, actor)
            # Serialize this actor's operation keys even across different spaces.
            db.scalar(select(Member).where(Member.id == actor).with_for_update())
            if action == "space.create":
                db.scalar(select(Platform).where(Platform.id == 1).with_for_update())
                space, membership = None, None
            elif action == "invite.respond":
                invitation = self.object(db, "invite", payload.get("id"), space_id)
                if invitation.recipient_id != actor:
                    raise PlatformError("invite_forbidden", "此邀请只允许指定账号处理。", 403)
                space = db.scalar(select(TeamSpace).where(TeamSpace.id == space_id).with_for_update())
                membership = None
            else:
                space, membership = self.access(db, space_id, actor, lock=True)
            old = db.get(SpaceOperation, key)
            if old:
                if old.request_hash != request_hash:
                    raise PlatformError("operation_conflict", "同一操作标识不能用于不同内容，请先确认原提交结果。", 409)
                return old.result
            result = self.dispatch(db, actor, space, membership, action, payload)
            db.flush()
            db.add(SpaceOperation(id=key, member_id=actor, space_id=space_id or result["id"],
                request_hash=request_hash, result=result))
        log.info("space.operation_completed member=%s space=%s operation=%s action=%s", actor,
                 space_id or result.get("id"), key, action)
        return result

    def dispatch(self, db, actor, space, membership, action, p):
        if action == "space.create":
            fields(p, {"name", "description"})
            space = TeamSpace(id=uid(), name=text(p.get("name"), 120, required=True),
                description=text(p.get("description", ""), 20000), owner_id=actor,
                revision=1, description_revision=1, event_version=0)
            db.add(space)
            db.flush()
            db.add(SpaceMember(space_id=space.id, member_id=actor, role="owner"))
            self.event(db, space, actor, "created", "space", space.id)
            return {"id": space.id, "revision": 1}
        if action == "invite.respond":
            fields(p, {"id", "revision", "response"})
            invitation = self.object(db, "invite", p.get("id"), space.id)
            version(invitation, p)
            if invitation.state != "pending" or invitation.expires_at <= time.time():
                raise PlatformError("invite_unavailable", "邀请已处理、撤销或过期，请联系空间管理员。", 409)
            if p.get("response") not in ("accept", "decline"):
                raise PlatformError("invalid_response", "请选择接受或拒绝邀请。")
            if p["response"] == "accept":
                self.active(space)
                existing = db.get(SpaceMember, (space.id, actor))
                if existing is None:
                    db.add(SpaceMember(space_id=space.id, member_id=actor, role=invitation.role))
                elif not existing.active:
                    existing.active, existing.role = True, invitation.role
                    existing.revision += 1
                invitation.state = "accepted"
            else:
                invitation.state = "declined"
            invitation.revision += 1
            self.event(db, space, actor, "invite." + invitation.state, "space", space.id)
            return {"id": invitation.id, "state": invitation.state}
        if action == "space.update":
            fields(p, {"revision", "name", "description", "model_mode", "model_ids", "allow_personal", "skills"})
            self.manager(membership)
            self.active(space)
            version(space, p)
            if "allow_personal" in p:
                if type(p["allow_personal"]) is not bool:
                    raise PlatformError("invalid_policy", "个人模型开关格式无效。")
                if p["allow_personal"] != space.allow_personal:
                    self.manager(membership, owner=True)
                space.allow_personal = p["allow_personal"]
            if "name" in p:
                space.name = text(p["name"], 120, required=True)
            if "description" in p:
                description = text(p["description"], 20000)
                if description != space.description:
                    space.description, space.description_revision = description, space.description_revision + 1
            if "model_mode" in p or "model_ids" in p:
                mode, ids = p.get("model_mode", space.model_mode), p.get("model_ids", space.model_ids)
                if mode not in ("authorized", "selected") or not isinstance(ids, list) or len(ids) > 100:
                    raise PlatformError("invalid_policy", "公司模型范围格式无效。")
                if any(not isinstance(i, str) or not db.get(Model, i) for i in ids):
                    raise PlatformError("model_unavailable", "所选公司模型已不存在。", 409)
                space.model_mode, space.model_ids = mode, list(dict.fromkeys(ids)) if mode == "selected" else []
            if "skills" in p:
                if not isinstance(p["skills"], list) or len(p["skills"]) > 30:
                    raise PlatformError("invalid_skills", "最多引用 30 项 Skill。")
                skills = []
                for skill in p["skills"]:
                    if not isinstance(skill, dict) or set(skill) != {"source", "id", "version"}:
                        raise PlatformError("invalid_skills", "Skill 只保存来源、标识和版本。")
                    skills.append({k: text(skill[k], 200, required=True) for k in skill})
                space.skills = skills
            space.revision += 1
            self.event(db, space, actor, "settings.updated", "space", space.id)
            return {"id": space.id, "revision": space.revision}
        if action in {"space.archive", "space.restore", "space.transfer"}:
            fields(p, {"revision", "member_id"})
            self.manager(membership, owner=True)
            version(space, p)
            if action == "space.transfer":
                target = self.active_member(db, space.id, p.get("member_id"))
                if target.member_id == actor:
                    raise PlatformError("invalid_owner", "请选择另一位有效成员。")
                target.role, membership.role, space.owner_id = "owner", "admin", target.member_id
                target.revision += 1
                membership.revision += 1
            else:
                space.state = "archived" if action == "space.archive" else "active"
            space.revision += 1
            self.event(db, space, actor, action, "space", space.id)
            return {"id": space.id, "revision": space.revision}
        if action in {"member.remove", "member.role", "space.leave"}:
            fields(p, {"member_id", "revision", "role"})
            target_id = actor if action == "space.leave" else p.get("member_id")
            target = db.get(SpaceMember, (space.id, target_id)) if isinstance(target_id, str) else None
            if not target or not target.active:
                raise PlatformError("member_unavailable", "该成员已经退出或移除。", 409)
            if action == "member.role":
                self.employee(db, target_id)
            version(target, p)
            if target.role == "owner":
                raise PlatformError("owner_transfer_required", "所有者须先转移所有权。", 409)
            if action != "space.leave":
                self.manager(membership, owner=target.role == "admin" or action == "member.role")
            if action == "member.role":
                self.active(space)
                if p.get("role") not in ("admin", "member"):
                    raise PlatformError("invalid_role", "角色必须为管理员或普通成员。")
                target.role = p["role"]
            else:
                target.active = False
            target.revision += 1
            self.event(db, space, actor, action, "space", space.id, {"member_id": target.member_id})
            return {"id": target.member_id, "revision": target.revision}
        if action in {"invite.create", "invite.revoke"}:
            self.manager(membership)
            if action == "invite.create":
                fields(p, {"member_id", "role"})
                self.active(space)
                target_id, role = p.get("member_id"), p.get("role", "member")
                self.employee(db, target_id)
                if role not in ("admin", "member"):
                    raise PlatformError("invalid_role", "邀请角色无效。")
                if role == "admin":
                    self.manager(membership, owner=True)
                current = db.get(SpaceMember, (space.id, target_id))
                if current and current.active:
                    raise PlatformError("already_member", "此账号已经是空间成员。", 409)
                if db.scalar(select(SpaceInvite).where(SpaceInvite.space_id == space.id,
                        SpaceInvite.recipient_id == target_id, SpaceInvite.state == "pending", SpaceInvite.expires_at > time.time())):
                    raise PlatformError("invite_pending", "已有待处理邀请，可先撤销再重新邀请。", 409)
                item = SpaceInvite(id=uid(), space_id=space.id, sender_id=actor, recipient_id=target_id,
                    role=role, expires_at=time.time() + 7 * 86400)
                db.add(item)
            else:
                fields(p, {"id", "revision"})
                item = self.object(db, "invite", p.get("id"), space.id)
                version(item, p)
                if item.role == "admin":
                    self.manager(membership, owner=True)
                if item.state != "pending":
                    raise PlatformError("invite_unavailable", "邀请已处理。", 409)
                item.state, item.revision = "revoked", item.revision + 1
            self.event(db, space, actor, action, "space", space.id)
            return {"id": item.id}
        if action.startswith("plan."):
            return self.plan_command(db, actor, space, membership, action, p)
        if action.startswith("comment."):
            return self.comment_command(db, actor, space, membership, action, p)
        if action.startswith("asset.") or action.startswith("upload.") or action == "folder.create":
            return self.asset_command(db, actor, space, membership, action, p)
        if action.startswith("share."):
            return self.share_command(db, actor, space, membership, action, p)
        raise PlatformError("unknown_action", "此操作尚不支持，请更新客户端。", 404)

    def plan_command(self, db, actor, space, membership, action, p):
        self.active(space)
        if action not in {"plan.create", "plan.update", "plan.status"}:
            raise PlatformError("unknown_action", "不支持此计划操作。", 404)
        if action == "plan.create":
            fields(p, {"title", "description", "assignee_id", "priority", "inputs"})
            plan = SpacePlan(id=uid(), space_id=space.id, title=text(p.get("title"), 200, required=True),
                author_id=actor, updated_by=actor, revision=1, state="pending", completion="", outcomes=[])
            db.add(plan)
        else:
            plan = self.object(db, "plan", p.get("id"), space.id)
            version(plan, p)
            if membership.role == "member" and actor not in ({plan.author_id, plan.assignee_id} if action == "plan.status" else {plan.author_id}):
                raise PlatformError("plan_forbidden", "只有计划创建人、负责人或空间管理员可维护此状态；内容修改由创建人或管理员处理。", 403)
        if action == "plan.status":
            fields(p, {"id", "revision", "state", "completion", "outcomes", "reason"})
            state = p.get("state")
            if state not in ("pending", "in_progress", "completed"):
                raise PlatformError("invalid_state", "计划状态无效。")
            reason = text(p.get("reason", ""), 2000, required=plan.state == "completed" and state != "completed")
            previous = {"state": plan.state, "completion": plan.completion, "outcomes": plan.outcomes}
            if state == "completed":
                plan.completion = text(p.get("completion", ""), 10000, required=True)
                plan.outcomes = self.outcome_refs(db, space.id, p.get("outcomes", []))
            plan.state = state
            self.event(db, space, actor, "plan.status", "plan", plan.id, {"previous": previous,
                "state": state, "reason": reason, "completion": plan.completion, "outcomes": plan.outcomes})
        else:
            if action == "plan.update":
                fields(p, {"id", "revision", "title", "description", "assignee_id", "priority", "inputs"})
            if "title" in p:
                plan.title = text(p["title"], 200, required=True)
            if "description" in p:
                plan.description = text(p["description"], 30000)
            if "assignee_id" in p:
                assignee = text(p["assignee_id"], 32)
                if assignee:
                    self.active_member(db, space.id, assignee)
                plan.assignee_id = assignee
            if "priority" in p:
                if p["priority"] not in ("normal", "high"):
                    raise PlatformError("invalid_priority", "优先级仅支持普通或高。")
                plan.priority = p["priority"]
            if "inputs" in p:
                plan.inputs = self.asset_refs(db, space.id, p["inputs"])
            self.event(db, space, actor, action, "plan", plan.id)
        if action != "plan.create":
            plan.revision += 1
        plan.updated_by, plan.updated_at = actor, time.time()
        return {"id": plan.id, "revision": plan.revision}

    def comment_command(self, db, actor, space, membership, action, p):
        if action not in {"comment.create", "comment.update", "comment.withdraw"}:
            raise PlatformError("unknown_action", "不支持此留言操作。", 404)
        if action != "comment.withdraw":
            self.active(space)
        if action == "comment.create":
            fields(p, {"target_kind", "target_id", "body"})
            kind, target = p.get("target_kind", "space"), p.get("target_id", space.id)
            self.target(db, space.id, kind, target)
            item = SpaceComment(id=uid(), space_id=space.id, author_id=actor, target_kind=kind,
                target_id=target, body=text(p.get("body"), 20000, required=True), revision=1)
            db.add(item)
        else:
            fields(p, {"id", "revision", "body", "reason"})
            item = self.object(db, "comment", p.get("id"), space.id)
            version(item, p)
            if item.author_id != actor:
                if action == "comment.update":
                    raise PlatformError("forbidden", "只能修改自己的留言。", 403)
                self.manager(membership)
                text(p.get("reason", ""), 2000, required=True)
            if item.state != "active":
                raise PlatformError("comment_withdrawn", "留言已撤回。", 409)
            if action == "comment.withdraw":
                item.state = "withdrawn"
            else:
                item.body = text(p.get("body"), 20000, required=True)
            item.revision += 1
            item.updated_at = time.time()
        self.event(db, space, actor, action, "comment", item.id, {"reason": p.get("reason", "")})
        return {"id": item.id, "revision": item.revision}

    def asset_command(self, db, actor, space, membership, action, p):
        if action not in {"folder.create", "upload.prepare", "upload.cancel", "asset.publish", "asset.update", "asset.withdraw", "asset.restore"}:
            raise PlatformError("unknown_action", "不支持此资料操作。", 404)
        if action not in {"asset.withdraw", "upload.cancel"}:
            self.active(space)
        if action == "folder.create":
            fields(p, {"name", "parent_id"})
            parent_id = text(p.get("parent_id", ""), 32)
            if parent_id:
                self.object(db, "folder", parent_id, space.id)
            folder_name = name(p.get("name"))
            if db.scalar(select(SpaceFolder).where(SpaceFolder.space_id == space.id,
                    SpaceFolder.parent_id == parent_id, SpaceFolder.name == folder_name)):
                raise PlatformError("name_conflict", "此文件夹已经存在。", 409)
            folder = SpaceFolder(id=uid(), space_id=space.id, parent_id=parent_id, name=folder_name, author_id=actor)
            db.add(folder)
            self.event(db, space, actor, action, "folder", folder.id)
            return {"id": folder.id}
        if action == "upload.prepare":
            fields(p, {"filename", "size", "sha256"})
            size, sha = p.get("size"), p.get("sha256")
            if type(size) is not int or not 0 <= size <= self.settings.max_space_file_bytes:
                raise PlatformError("file_too_large", f"单文件不能超过 {self.settings.max_space_file_bytes // 1024 // 1024} MiB。", 413)
            if not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha):
                raise PlatformError("invalid_digest", "文件摘要格式无效。")
            self.check_quota(db, space.id, size)
            item = SpaceUpload(id=uid(), space_id=space.id, author_id=actor, filename=name(p.get("filename")), size=size, sha256=sha)
            db.add(item)
            return {"id": item.id, "state": "pending"}
        if action == "upload.cancel":
            fields(p, {"id"})
            item = self.object(db, "upload", p.get("id"), space.id)
            if item.author_id != actor or item.state == "published":
                raise PlatformError("upload_unavailable", "此上传不能取消。", 409)
            item.state, item.generation, item.updated_at = "cancelled", uid(), time.time()
            return {"id": item.id, "state": item.state}
        if action == "asset.publish":
            fields(p, {"upload_id", "asset_id", "revision", "name", "description", "folder_id", "keep_both"})
            item, revision = self.publish_asset(db, actor, space, membership, p)
            self.event(db, space, actor, "asset.published", "asset", item.id)
            return {"id": item.id, "revision": item.revision, "revision_id": revision.id}
        fields(p, {"id", "revision", "name", "description", "folder_id", "reason"})
        item = self.object(db, "asset", p.get("id"), space.id)
        version(item, p)
        if item.author_id != actor:
            self.manager(membership)
            if action == "asset.withdraw":
                text(p.get("reason", ""), 2000, required=True)
        if action == "asset.update":
            if "name" in p:
                item.name = name(p["name"])
            if "description" in p:
                item.description = text(p["description"], 10000)
            if "folder_id" in p:
                folder_id = text(p["folder_id"], 32)
                if folder_id:
                    self.object(db, "folder", folder_id, space.id)
                item.folder_id = folder_id
            if db.scalar(select(SpaceAsset.id).where(SpaceAsset.space_id == space.id,
                    SpaceAsset.folder_id == item.folder_id, SpaceAsset.name == item.name,
                    SpaceAsset.state == "active", SpaceAsset.id != item.id)):
                raise PlatformError("name_conflict", "目标文件夹已有同名资料，请使用其他名称。", 409)
        else:
            item.state = "withdrawn" if action == "asset.withdraw" else "active"
        item.revision += 1
        item.updated_at = time.time()
        self.event(db, space, actor, action, "asset", item.id, {"reason": p.get("reason", "")})
        return {"id": item.id, "revision": item.revision}

    def publish_asset(self, db, actor, space, membership, p, *, origin_share=""):
        upload = self.object(db, "upload", p.get("upload_id"), space.id)
        if upload.author_id != actor or upload.state != "ready":
            raise PlatformError("upload_not_ready", "附件尚未完整上传，或不属于当前账号。", 409)
        self.verify_file(upload)
        if p.get("asset_id"):
            item = self.object(db, "asset", p["asset_id"], space.id)
            version(item, p)
            if item.author_id != actor:
                self.manager(membership)
            if item.state != "active":
                raise PlatformError("asset_withdrawn", "请先恢复此资料。", 409)
            item.latest += 1
            item.revision += 1
        else:
            folder_id, filename = text(p.get("folder_id", ""), 32), name(p.get("name", upload.filename))
            if folder_id:
                self.object(db, "folder", folder_id, space.id)
            conflicts = list(db.scalars(select(SpaceAsset.name).where(SpaceAsset.space_id == space.id,
                SpaceAsset.folder_id == folder_id, SpaceAsset.state == "active")))
            if filename in conflicts:
                if p.get("keep_both") is not True:
                    raise PlatformError("name_conflict", "已有同名文件，请选择保留两份或更新指定文件。", 409)
                stem, suffix = os.path.splitext(filename)
                counter = 2
                while filename in conflicts:
                    filename = f"{stem[:160]} ({counter}){suffix[:20]}"
                    counter += 1
            item = SpaceAsset(id=uid(), space_id=space.id, folder_id=folder_id, name=filename,
                author_id=actor, description=text(p.get("description", ""), 10000), revision=1,
                latest=1, origin_share_id=origin_share)
            db.add(item)
            db.flush()
        revision = SpaceAssetRevision(id=uid(), space_id=space.id, asset_id=item.id,
            number=item.latest, upload_id=upload.id, author_id=actor, size=upload.size, sha256=upload.sha256)
        db.add(revision)
        upload.state, upload.updated_at, item.updated_at = "published", time.time(), time.time()
        return item, revision

    def share_command(self, db, actor, space, membership, action, p):
        if action not in {"share.publish", "share.withdraw"}:
            raise PlatformError("unknown_action", "不支持此成果操作。", 404)
        if action == "share.withdraw":
            fields(p, {"id", "revision", "reason"})
            share = self.object(db, "share", p.get("id"), space.id)
            version(share, p)
            if share.author_id != actor:
                self.manager(membership)
                text(p.get("reason", ""), 2000, required=True)
            share.state, share.revision, share.updated_at = "withdrawn", share.revision + 1, time.time()
            retained = []
            for asset in db.scalars(select(SpaceAsset).where(SpaceAsset.origin_share_id == share.id)):
                if self.asset_references(db, space.id, asset.id, exclude_share=share.id):
                    retained.append(asset.id)
                else:
                    asset.state, asset.revision, asset.updated_at = "withdrawn", asset.revision + 1, time.time()
            self.event(db, space, actor, action, "share", share.id, {"reason": p.get("reason", ""), "retained_assets": retained})
            return {"id": share.id, "revision": share.revision, "retained_assets": retained}
        self.active(space)
        fields(p, {"id", "revision", "title", "plan_id", "body", "assets", "uploads", "source_state", "source_ref"})
        title, body = text(p.get("title"), 200, required=True), text(p.get("body", ""), 200000)
        plan_id = text(p.get("plan_id", ""), 32)
        if plan_id:
            self.object(db, "plan", plan_id, space.id)
        if p.get("source_state") not in ("completed", "running", "stopped", "failed", "manual"):
            raise PlatformError("invalid_source_state", "请保留来源任务的实际状态。")
        refs = self.asset_refs(db, space.id, p.get("assets", []))
        uploads = p.get("uploads", [])
        if not isinstance(uploads, list) or len(uploads) + len(refs) > 100 or any(not isinstance(i, str) for i in uploads) or len(set(uploads)) != len(uploads):
            raise PlatformError("invalid_attachments", "附件列表无效，一次最多 100 份。")
        if not body and not refs and not uploads:
            raise PlatformError("empty_share", "请选择文字或文件后发布。")
        if p.get("id"):
            share = self.object(db, "share", p["id"], space.id)
            version(share, p)
            if share.author_id != actor or share.state != "active":
                raise PlatformError("share_forbidden", "只能更新自己的有效成果。", 403)
            share.title, share.latest, share.revision, share.updated_at = title, share.latest + 1, share.revision + 1, time.time()
            share.plan_id = plan_id
        else:
            share = SpaceShare(id=uid(), space_id=space.id, author_id=actor, title=title,
                plan_id=plan_id, latest=1, revision=1)
            db.add(share)
            db.flush()
        for upload_id in uploads:
            asset, revision = self.publish_asset(db, actor, space, membership,
                {"upload_id": upload_id, "keep_both": True}, origin_share=share.id)
            refs.append({"asset_id": asset.id, "revision_id": revision.id})
        snapshot = SpaceSnapshot(id=uid(), space_id=space.id, share_id=share.id, number=share.latest,
            body=body, assets=refs, source_state=p["source_state"], source_ref=text(p.get("source_ref", ""), 128))
        db.add(snapshot)
        self.event(db, space, actor, action, "share", share.id)
        return {"id": share.id, "revision": share.revision, "snapshot_id": snapshot.id}

    def withdrawal_preview(self, actor, space_id, share_id):
        with self.database.transaction() as db:
            _, membership = self.access(db, space_id, actor)
            share = self.object(db, "share", share_id, space_id)
            if share.author_id != actor:
                self.manager(membership)
            attachments = []
            ids = {r["asset_id"] for snapshot in db.scalars(select(SpaceSnapshot).where(SpaceSnapshot.share_id == share_id)) for r in snapshot.assets}
            for asset_id in ids:
                asset = self.object(db, "asset", asset_id, space_id)
                references = self.asset_references(db, space_id, asset_id, exclude_share=share_id)
                attachments.append({"id": asset.id, "name": asset.name, "retained": asset.origin_share_id != share_id or bool(references), "references": references})
            return {"revision": share.revision, "attachments": attachments}

    def check_quota(self, db, space_id, additional=0, exclude=""):
        # Expired uncommitted reservations no longer occupy logical quota.
        used = db.scalar(select(func.coalesce(func.sum(SpaceUpload.size), 0)).where(SpaceUpload.space_id == space_id,
            SpaceUpload.id != exclude, SpaceUpload.state.in_(["pending", "receiving", "ready", "published"]),
            (SpaceUpload.state == "published") | (SpaceUpload.updated_at > time.time() - 86400)))
        if used + additional > self.settings.max_space_bytes:
            raise PlatformError("space_quota_exceeded", "空间容量不足，请联系平台管理员调整容量；已选择文件仍保留。", 413)

    def file_path(self, upload_id):
        if not re.fullmatch(r"[a-f0-9]{32}", upload_id):
            raise PlatformError("invalid_file", "文件标识无效。")
        return self.root / (upload_id + ".blob")

    def verify_file(self, upload):
        path = self.file_path(upload.id)
        try:
            if not path.is_file() or path.stat().st_size != upload.size:
                raise OSError()
        except OSError:
            raise PlatformError("file_missing", "共享文件不完整，请重新上传或联系管理员恢复。", 503) from None
        return path

    async def receive(self, actor, space_id, upload_id, stream):
        with self.transaction() as db:
            space, _ = self.access(db, space_id, actor, lock=True)
            self.active(space)
            upload = self.object(db, "upload", upload_id, space_id)
            if upload.author_id != actor or upload.state in {"published", "cancelled", "receiving"}:
                raise PlatformError("upload_unavailable", "上传正在处理、已取消或已经发布，请核对原操作。", 409)
            if upload.state == "ready":
                self.verify_file(upload)
                return {"id": upload.id, "state": "ready"}
            self.check_quota(db, space_id, upload.size, exclude=upload.id)
            upload.state, upload.generation, upload.error, upload.updated_at = "receiving", uid(), "", time.time()
            generation, expected_size, expected_hash = upload.generation, upload.size, upload.sha256
        temporary = self.root / (upload_id + "." + generation + ".part")
        total, hasher = 0, hashlib.sha256()
        try:
            with temporary.open("xb") as output:
                async for chunk in stream:
                    total += len(chunk)
                    if total > expected_size:
                        raise PlatformError("file_size_mismatch", "文件大小与上传前不一致，请重新选择文件。", 409)
                    hasher.update(chunk)
                    await asyncio.to_thread(output.write, chunk)
                await asyncio.to_thread(output.flush)
                await asyncio.to_thread(os.fsync, output.fileno())
            if total != expected_size or hasher.hexdigest() != expected_hash:
                raise PlatformError("file_digest_mismatch", "文件不完整或上传期间已改变，请重新选择文件。", 409)
            with self.transaction() as db:
                space, _ = self.access(db, space_id, actor, lock=True)
                self.active(space)
                upload = self.object(db, "upload", upload_id, space_id)
                if upload.generation != generation or upload.state != "receiving":
                    raise PlatformError("upload_cancelled", "此上传已取消，迟到结果不会发布。", 409)
                os.replace(temporary, self.file_path(upload_id))
                upload.state, upload.updated_at = "ready", time.time()
                self.database.audit(db, "space.upload.received", member_id=actor, target=space_id + ":" + upload_id)
            return {"id": upload_id, "state": "ready"}
        except BaseException as exc:
            with self.transaction() as db:
                upload = db.get(SpaceUpload, upload_id)
                if upload and upload.generation == generation and upload.state == "receiving":
                    upload.state, upload.error, upload.updated_at = "failed", "上传未完成，原文件仍保留，可重新上传。", time.time()
            log.warning("space.upload_failed space=%s upload=%s reason=%s", space_id, upload_id, type(exc).__name__)
            if isinstance(exc, OSError):
                raise PlatformError("upload_storage_failed", "中心存储未完成写入，请检查容量后重试。", 503) from None
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def download(self, actor, space_id, revision_id):
        with self.database.transaction() as db:
            self.access(db, space_id, actor)
            revision = self.object(db, "asset_revision", revision_id, space_id)
            asset = self.object(db, "asset", revision.asset_id, space_id)
            if asset.state != "active":
                raise PlatformError("asset_withdrawn", "资料已撤下，停止新的下载和 AI 读取。", 410)
            upload = self.object(db, "upload", revision.upload_id, space_id)
            return self.verify_file(upload), asset.name, revision.sha256

    def policy(self, actor, space_id, source, model_id="", *, assets=(), snapshots=()):
        with self.database.transaction() as db:
            space, _ = self.access(db, space_id, actor)
            if source == "personal":
                allowed = space.allow_personal
            elif source == "enterprise":
                model = db.get(Model, model_id) if isinstance(model_id, str) else None
                allowed = bool(model and model.enabled and db.get(ModelGrant, (actor, model_id))
                    and (space.model_mode == "authorized" or model_id in space.model_ids))
            else:
                raise PlatformError("invalid_model_source", "模型来源无效。")
            if not allowed:
                raise PlatformError("space_model_forbidden", "当前空间不允许此模型，或你的公司模型授权已失效。输入和已有输出仍保留，请选择获准模型后继续。", 403)
            self.asset_refs(db, space_id, list(assets) if isinstance(assets, tuple) else assets)
            self.outcome_refs(db, space_id, list(snapshots) if isinstance(snapshots, tuple) else snapshots)
            return {"allowed": True, "revision": space.revision, "space_id": space.id, "state": space.state}

    def recover_uploads(self):
        with self.transaction() as db:
            db.execute(update(SpaceUpload).where(SpaceUpload.state == "receiving").values(
                state="failed", error="中心服务中断，请重新上传完整文件。", updated_at=time.time()))
        for path in self.root.glob("*.part"):
            if re.fullmatch(r"[a-f0-9]{32}\.[a-f0-9]{32}\.part", path.name):
                path.unlink(missing_ok=True)

    def cleanup_uploads(self):
        """Release expired unpublished bytes; the same frozen upload can retry."""
        with self.transaction() as db:
            uploads = list(db.scalars(select(SpaceUpload).where(
                SpaceUpload.state.in_(["pending", "failed", "cancelled", "ready"]),
                SpaceUpload.updated_at < time.time() - 86400).with_for_update()))
            for upload in uploads:
                self.file_path(upload.id).unlink(missing_ok=True)
                if upload.state != "cancelled":
                    upload.state = "failed"
                    upload.error = "未发布的临时文件已过期，请重试原上传；已发布内容不受影响。"
                upload.updated_at = time.time()
        if uploads:
            log.info("space.upload_cleanup count=%s", len(uploads))
        return len(uploads)

    def verify_storage(self):
        """Check DB/file consistency after a coordinated backup restoration."""
        failures, checked = [], 0
        with self.database.transaction() as db:
            for upload in db.scalars(select(SpaceUpload).where(SpaceUpload.state == "published")):
                checked += 1
                try:
                    path = self.verify_file(upload)
                    with path.open("rb") as stream:
                        hasher = hashlib.sha256()
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            hasher.update(chunk)
                    if hasher.hexdigest() != upload.sha256:
                        failures.append({"id": upload.id, "reason": "digest_mismatch"})
                except (OSError, PlatformError):
                    failures.append({"id": upload.id, "reason": "file_missing"})
        return {"checked": checked, "failures": failures}

    def owner_recovery_list(self, actor, member_id):
        with self.database.transaction() as db:
            admin = db.get(Member, actor)
            if not admin or not admin.enabled or admin.role != "admin":
                raise PlatformError("forbidden", "需要平台管理员权限。", 403)
            owner = db.get(Member, member_id)
            if not owner or owner.enabled:
                raise PlatformError("owner_active", "此入口仅处理所有者已停用的空间。", 409)
            return {"items": [{"id": s.id, "name": s.name, "revision": s.revision,
                "candidates": [{"id": m.member_id, "name": u.name} for m, u in db.execute(
                    select(SpaceMember, Member).join(Member, Member.id == SpaceMember.member_id).where(
                        SpaceMember.space_id == s.id, SpaceMember.active, Member.enabled))]}
                for s in db.scalars(select(TeamSpace).where(TeamSpace.owner_id == member_id))]}

    def recover_owner(self, actor, space_id, p):
        fields(p, {"revision", "member_id", "reason"})
        reason = text(p.get("reason", ""), 2000, required=True)
        operation_id = text(p.get("operation_id"), 128, required=True)
        key, hashed = digest(actor + ":" + operation_id), digest(encode({"space": space_id, "payload": p, "action": "owner.recover"}))
        with self.transaction() as db:
            admin = db.get(Member, actor)
            if not admin or not admin.enabled or admin.role != "admin":
                raise PlatformError("forbidden", "需要平台管理员权限。", 403)
            db.scalar(select(Member).where(Member.id == actor).with_for_update())
            space = db.scalar(select(TeamSpace).where(TeamSpace.id == space_id).with_for_update())
            old = db.get(SpaceOperation, key)
            if old:
                if old.request_hash != hashed:
                    raise PlatformError("operation_conflict", "操作标识已用于其他内容。", 409)
                return old.result
            if not space:
                raise PlatformError("not_found", "空间不存在。", 404)
            owner = db.get(Member, space.owner_id)
            if owner and owner.enabled:
                raise PlatformError("owner_active", "当前所有者仍有效，应由本人转移所有权。", 409)
            version(space, p)
            target = self.active_member(db, space_id, p.get("member_id"))
            previous = db.get(SpaceMember, (space_id, space.owner_id))
            if previous:
                previous.role, previous.revision = "member", previous.revision + 1
            target.role, target.revision = "owner", target.revision + 1
            old_owner, space.owner_id, space.revision = space.owner_id, target.member_id, space.revision + 1
            self.event(db, space, actor, "owner.recovered", "space", space.id,
                {"previous_owner": old_owner, "new_owner": target.member_id, "reason": reason})
            result = {"id": space.id, "revision": space.revision}
            db.add(SpaceOperation(id=key, member_id=actor, space_id=space.id, request_hash=hashed, result=result))
            return result
