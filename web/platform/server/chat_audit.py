"""One sanitized latest request per member and chat, separate from local history."""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from tempfile import SpooledTemporaryFile

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Query, Request
from starlette.responses import StreamingResponse
from sqlalchemy import BigInteger, Boolean, Float, JSON, String, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, mapped_column

from .database import AuditEvent, Base, Member
from .security import PlatformError, digest, encode


RETENTION = 30 * 86400
EVENT_RETENTION = 90 * 86400
EXCEL_RECORDS_PER_SHEET = 1_048_575  # Excel reserves one of its 1,048,576 rows for our header.
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]{1,240}")
PROTOCOLS = {"chat_completions", "responses", "anthropic"}
SECRET_KEYS = {"authorization", "proxy_authorization", "cookie", "set_cookie", "extra_headers", "headers",
               "access_token", "refresh_token", "id_token", "token", "api_key", "x_api_key", "password", "passwd",
               "client_secret", "private_key", "secret_key", "credentials", "http_client", "secret", "_cowork"}
MEDIA_KEYS = {"file_data", "image_data", "audio_data", "video_data", "base64", "b64_json", "input_audio",
              "input_image", "input_video", "image_url", "file_url", "file_bytes", "file_content", "attachments"}
MEDIA_TYPES = {"input_file", "output_file", "file", "input_image", "image_url", "image", "input_audio", "audio",
               "input_video", "video", "document", "base64", "computer_screenshot"}
DATA_URL = re.compile(r"(?i)data:[^\s,;\"']{0,128}(?:;[^\s,;\"']{1,128})*,[^\s\"'<>`]+")
SECRET_TEXT = re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|token)"
                         r"\b[\"']?\s*[:=]\s*)(?:\"(?:[^\"\\]|\\.)*\"|'[^']*'|[^\s,;&}\"']+)")
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
INLINE_MEDIA = re.compile(r"(?<![A-Za-z0-9+/])(?:[A-Za-z0-9+/]{1024,}={0,2})(?![A-Za-z0-9+/])")


def sanitize(value, depth=0):
    if depth > 64:
        return "[嵌套内容已省略]"
    if isinstance(value, dict):
        media = value.get("type")
        if isinstance(media, str) and media in MEDIA_TYPES:
            return {"type": media, "content": "[文件或媒体内容已省略]"}
        safe = {}
        for key, item in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in SECRET_KEYS:
                continue
            safe[key] = "[文件或媒体内容已省略]" if normalized in MEDIA_KEYS else sanitize(item, depth + 1)
        return safe
    if isinstance(value, list):
        return [sanitize(item, depth + 1) for item in value]
    if isinstance(value, str):
        value = DATA_URL.sub("[媒体内容已省略]", value)
        value = INLINE_MEDIA.sub("[疑似内嵌媒体内容已省略]", value)
        value = BEARER.sub("Bearer [已隐藏]", value)
        return SECRET_TEXT.sub(r"\1[已隐藏]", value)
    return value


class ChatAuditPolicy(Base):
    __tablename__ = "chat_audit_policy"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    personal_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(default=0)


class ChatAuditRequest(Base):
    __tablename__ = "chat_audit_requests"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(32), index=True)
    conversation_id: Mapped[str] = mapped_column(String(240), index=True)
    request_id: Mapped[str] = mapped_column(String(240))
    model_name: Mapped[str] = mapped_column(String(256))
    model_source: Mapped[str] = mapped_column(String(12))
    protocol: Mapped[str] = mapped_column(String(32))
    captured_us: Mapped[int] = mapped_column(BigInteger, index=True)
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    parameters: Mapped[dict] = mapped_column(JSON)
    omitted: Mapped[bool] = mapped_column(Boolean, default=False)


def identifier(value):
    return isinstance(value, str) and bool(IDENTIFIER.fullmatch(value))


class ChatAudit:
    def __init__(self, settings, database, vault, management):
        self.platform_id = settings.platform_id
        self.database = database
        self.vault = vault
        self.management = management

    def policy(self):
        with self.database.transaction() as db:
            row = db.get(ChatAuditPolicy, 1)
            return {"personal_enabled": row.personal_enabled if row else False,
                    "revision": row.revision if row else 0}

    def set_policy(self, actor, body):
        if set(body) != {"personal_enabled", "revision"} or type(body["personal_enabled"]) is not bool or type(body["revision"]) is not int:
            raise PlatformError("invalid_audit_policy", "审计设置无效，请刷新页面后重试。")
        with self.database.transaction() as db:
            insert = sqlite_insert if self.database.engine.dialect.name == "sqlite" else pg_insert
            db.execute(insert(ChatAuditPolicy).values(id=1, personal_enabled=False, revision=0)
                       .on_conflict_do_nothing(index_elements=[ChatAuditPolicy.id]))
            changed = db.execute(update(ChatAuditPolicy).where(ChatAuditPolicy.id == 1, ChatAuditPolicy.revision == body["revision"])
                                 .values(personal_enabled=body["personal_enabled"], revision=body["revision"] + 1)).rowcount
            if not changed:
                raise PlatformError("audit_policy_changed", "审计设置已改变，请刷新后重试。", 409)
            self.database.audit(db, "chat_audit.policy_updated", member_id=actor,
                                target="personal_model", outcome="enabled" if body["personal_enabled"] else "disabled")
        return self.policy()

    def permit(self, actor, body, team_spaces, capabilities):
        if not self.policy()["personal_enabled"]:
            return {"enabled": False}
        conversation, run_id = body.get("conversation_id"), body.get("run_id")
        space_id, items = body.get("space_id"), body.get("capabilities")
        if not identifier(conversation) or not identifier(run_id) or set(body) - {"conversation_id", "run_id", "space_id", "capabilities"}:
            raise PlatformError("invalid_audit_scope", "聊天审计归属无效。")
        if space_id:
            if not identifier(space_id):
                raise PlatformError("invalid_audit_scope", "聊天审计归属无效。")
            team_spaces.policy(actor, space_id, "personal")
        elif isinstance(items, list) and items:
            capabilities().check(actor, {"items": items})
        else:
            raise PlatformError("audit_scope_forbidden", "只可上报已授权企业任务的请求。", 403)
        issued = time.time()
        return {"enabled": True, "permit": self.vault.seal({"type": "chat_audit_personal", "platform_id": self.platform_id,
                "member_id": actor, "conversation_id": conversation, "run_id": run_id,
                "issued_at": issued, "expires_at": issued + RETENTION})}

    def receive(self, actor, body):
        through = {"conversation_id", "request_id", "run_id", "captured_us", "protocol", "model_name", "model_source",
                   "model_id", "parameters", "permit", "omitted"}
        conversation, request_id, run_id = (body.get(key) for key in ("conversation_id", "request_id", "run_id"))
        captured = body.get("captured_us")
        source, model, protocol = (body.get(key) for key in ("model_source", "model_name", "protocol"))
        omitted = body.get("omitted", False)
        parameters = body.get("parameters")
        if (set(body) - through or not all(identifier(value) for value in (conversation, request_id, run_id))
                or type(captured) is not int or not 0 < captured < 10**16 or not isinstance(source, str)
                or source not in {"personal", "enterprise"}
                or not isinstance(model, str) or not 1 <= len(model) <= 256 or not isinstance(protocol, str)
                or protocol not in PROTOCOLS or not isinstance(parameters, dict) or type(omitted) is not bool):
            raise PlatformError("invalid_chat_audit", "聊天请求记录格式无效。")
        now = time.time()
        timestamp = captured / 1_000_000
        if not now - RETENTION < timestamp <= now + 300:
            raise PlatformError("chat_audit_expired", "聊天请求已超出留存期限或本机时间不正确。", 409)
        if source == "enterprise":
            if not identifier(body.get("model_id")):
                raise PlatformError("invalid_chat_audit", "企业模型标识无效。")
            model_info = self.management.authorized_model(actor, body["model_id"])
            if model_info["model_name"] != model or model_info["protocol"] != protocol:
                raise PlatformError("audit_model_changed", "企业模型配置不匹配。", 409)
        else:
            permit = body.get("permit")
            try:
                if not isinstance(permit, str) or len(permit) > 4096:
                    raise ValueError()
                data = self.vault.open(permit)
                if (data["type"] != "chat_audit_personal" or data["platform_id"] != self.platform_id
                        or data["member_id"] != actor or data["conversation_id"] != conversation
                        or data["run_id"] != run_id or data["expires_at"] <= now or timestamp < data["issued_at"] - 300):
                    raise ValueError()
            except (InvalidToken, KeyError, TypeError, ValueError):
                raise PlatformError("audit_scope_forbidden", "此请求没有有效的企业任务审计许可。", 403) from None
        parameters = {} if omitted else sanitize(parameters)
        if len(encode(parameters).encode("utf-8")) > MAX_PAYLOAD_BYTES:
            parameters, omitted = {}, True
        model = sanitize(model)[:256]
        record_id = digest(encode([self.platform_id, actor, conversation]))
        values = {"request_id": request_id, "model_source": source, "model_name": model, "protocol": protocol,
                  "captured_us": captured, "expires_at": timestamp + RETENTION, "parameters": parameters, "omitted": omitted}
        factory = sqlite_insert if self.database.engine.dialect.name == "sqlite" else pg_insert
        with self.database.transaction() as db:
            statement = factory(ChatAuditRequest).values(id=record_id, member_id=actor, conversation_id=conversation, **values)
            db.execute(statement.on_conflict_do_update(index_elements=[ChatAuditRequest.id], set_=values,
                where=or_(ChatAuditRequest.captured_us < captured,
                          and_(ChatAuditRequest.captured_us == captured, ChatAuditRequest.request_id < request_id))))
        return {"received": True, "id": record_id}

    def cleanup(self, db, now):
        count = db.execute(delete(ChatAuditRequest).where(ChatAuditRequest.expires_at <= now)).rowcount
        db.execute(delete(AuditEvent).where(AuditEvent.created_at <= now - EVENT_RETENTION))
        if count:
            self.database.audit(db, "chat_audit.expired", target=str(count))
        return count

    @staticmethod
    def view(row, member_name):
        return {"id": row.id, "member_id": row.member_id, "member_name": member_name or row.member_id,
                "conversation_id": row.conversation_id, "captured_at": row.captured_us / 1_000_000,
                "expires_at": row.expires_at, "model_name": row.model_name, "model_source": row.model_source,
                "protocol": row.protocol, "omitted": row.omitted}

    @staticmethod
    def filters(events, member_id, keyword, since, until):
        if since is not None and until is not None and since >= until:
            raise PlatformError("invalid_audit_range", "结束时间应晚于开始时间。")
        row = AuditEvent if events else ChatAuditRequest
        filters = [row.created_at > time.time() - EVENT_RETENTION] if events else [row.expires_at > time.time()]
        if member_id:
            filters.append(row.member_id == member_id)
        if keyword:
            filters.append(row.action.contains(keyword, autoescape=True) if events else or_(
                row.conversation_id.contains(keyword, autoescape=True), row.model_name.contains(keyword, autoescape=True)))
        if since is not None:
            filters.append(row.created_at >= since if events else row.captured_us >= int(since * 1_000_000))
        if until is not None:
            filters.append(row.created_at < until if events else row.captured_us < int(until * 1_000_000))
        return filters

    def list(self, *, member_id, keyword, since, until, offset, limit):
        row = ChatAuditRequest
        filters = self.filters(False, member_id, keyword, since, until)
        with self.database.transaction() as db:
            total = db.scalar(select(func.count()).select_from(row).where(*filters))
            # Selecting just displayed columns avoids pulling all stored request bodies into the listing.
            rows = db.execute(select(row.id, row.member_id, row.conversation_id, row.captured_us, row.expires_at,
                row.model_name, row.model_source, row.protocol, row.omitted, Member.name.label("member_name"))
                .outerjoin(Member, row.member_id == Member.id).where(*filters)
                .order_by(row.captured_us.desc(), row.id).offset(offset).limit(limit)).all()
            return {"total": total, "items": [self.view(item, item.member_name) for item in rows]}

    def detail(self, actor, record_id):
        with self.database.transaction() as db:
            row = db.get(ChatAuditRequest, record_id)
            if not row or row.expires_at <= time.time():
                raise PlatformError("chat_audit_missing", "聊天请求不存在或已到期清理。", 404)
            member = db.get(Member, row.member_id)
            self.database.audit(db, "chat_audit.read", member_id=actor, target=record_id)
            return {**self.view(row, member.name if member else ""), "request_id": row.request_id, "parameters": row.parameters}

    def events(self, *, member_id, keyword, offset, limit, since=None, until=None):
        row = AuditEvent
        filters = self.filters(True, member_id, keyword, since, until)
        with self.database.transaction() as db:
            total = db.scalar(select(func.count()).select_from(row).where(*filters))
            rows = db.execute(select(row, Member.name).outerjoin(Member, Member.id == row.member_id)
                              .where(*filters).order_by(row.created_at.desc(), row.id).offset(offset).limit(limit))
            return {"total": total, "items": [{"id": item.id, "action": item.action, "target": item.target,
                "outcome": item.outcome, "member_name": name or item.member_id, "created_at": item.created_at}
                for item, name in rows]}

    def export(self, actor, kind, member_id, keyword, since, until):
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
        from openpyxl.styles import Font

        events = kind == "events"
        if not events and kind != "chats":
            raise PlatformError("invalid_export", "请选择聊天请求或平台操作。")
        row = AuditEvent if events else ChatAuditRequest
        filters = self.filters(events, member_id, keyword, since, until)
        stamp = row.created_at if events else row.captured_us
        cols = [row.action, row.target, row.outcome] if events else [
            row.conversation_id, row.model_name, row.model_source, row.protocol, row.omitted]
        stmt = (select(stamp, Member.name, row.member_id, *cols).outerjoin(Member, Member.id == row.member_id)
                .where(*filters).order_by(stamp.desc(), row.id).execution_options(yield_per=500))
        book = Workbook(write_only=True)
        sheet_title = "平台操作" if events else "聊天请求"
        def append(ws, values, header=False):
            cells = []
            for value in values:
                cell = WriteOnlyCell(ws, value=value if not isinstance(value, str) else ILLEGAL_CHARACTERS_RE.sub("", value))
                if isinstance(value, str):
                    cell.data_type = "s"
                if header:
                    cell.font = Font(bold=True)
                cells.append(cell)
            ws.append(cells)
        zone = timezone(timedelta(hours=8))
        def local_date(t):
            return datetime.fromtimestamp(t, zone).replace(tzinfo=None)
        columns = ["时间（北京时间）", "人员", "成员 ID"] + (
            ["操作", "对象", "结果"] if events else ["聊天标识", "模型", "来源", "协议", "正文已省略"])
        sheet = book.create_sheet(sheet_title)
        append(sheet, columns, True)
        output = SpooledTemporaryFile(max_size=2**20)
        try:
            with self.database.transaction() as db:
                count = 0
                for t, name, mid, *values in db.execute(stmt):
                    if count and count % EXCEL_RECORDS_PER_SHEET == 0:
                        sheet = book.create_sheet(f"{sheet_title} {count // EXCEL_RECORDS_PER_SHEET + 1}")
                        append(sheet, columns, True)
                    append(sheet, [local_date(t if events else t / 1_000_000), name or mid, mid, *values])
                    count += 1
                notes = book.create_sheet("筛选条件")
                member = db.get(Member, member_id) if member_id else None
                for key, value in [("人员", member.name if member else member_id or "全部人员"), ("关键词", keyword),
                                   ("开始时间（北京时间）", local_date(since) if since is not None else "不限"),
                                   ("截止时间（不含，北京时间）", local_date(until) if until is not None else "不限"),
                                   ("记录数", count)]:
                    append(notes, [key, value])
                book.save(output)
                self.database.audit(db, "audit.exported", member_id=actor, target=f"{kind}/{count}")
            output.seek(0)
        except BaseException:
            output.close()
            raise
        def chunks():
            with output:
                while data := output.read(65536):
                    yield data
        return StreamingResponse(chunks(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="audit-{kind}.xlsx"', "Cache-Control": "no-store"})


def chat_audit_router(audit, principal, read_json, team_spaces, capabilities):
    router = APIRouter(prefix="/api/v1")

    @router.post("/chat-audit/policy")
    async def issue_permit(request: Request):
        return audit.permit(principal(request)["id"], await read_json(request, 65536), team_spaces, capabilities)

    @router.put("/chat-audit/latest")
    async def upload(request: Request):
        actor = principal(request)
        return await asyncio.to_thread(audit.receive, actor["id"], await read_json(request, MAX_PAYLOAD_BYTES + 16384))

    @router.get("/admin/chat-audit/policy")
    def read_policy(request: Request):
        principal(request, admin=True)
        return audit.policy()

    @router.put("/admin/chat-audit/policy")
    async def save_policy(request: Request):
        return audit.set_policy(principal(request, admin=True)["id"], await read_json(request, 2048))

    @router.get("/admin/chat-audit")
    def list_requests(request: Request, member_id: str = Query("", max_length=32), keyword: str = Query("", max_length=240),
                      since: float | None = Query(None, ge=0, le=9999999999), until: float | None = Query(None, ge=0, le=9999999999),
                      offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        principal(request, admin=True)
        return audit.list(member_id=member_id, keyword=keyword, since=since, until=until, offset=offset, limit=limit)

    @router.get("/admin/chat-audit/events")
    def list_events(request: Request, member_id: str = Query("", max_length=32), keyword: str = Query("", max_length=80),
                    since: float | None = Query(None, ge=0, le=9999999999), until: float | None = Query(None, ge=0, le=9999999999),
                    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        principal(request, admin=True)
        return audit.events(member_id=member_id, keyword=keyword, since=since, until=until, offset=offset, limit=limit)

    @router.get("/admin/chat-audit/export.xlsx")
    def export(request: Request, kind: str, member_id: str = Query("", max_length=32), keyword: str = Query("", max_length=240),
               since: float | None = Query(None, ge=0, le=9999999999), until: float | None = Query(None, ge=0, le=9999999999)):
        actor = principal(request, admin=True)
        return audit.export(actor["id"], kind, member_id, keyword, since, until)

    @router.get("/admin/chat-audit/{record_id}")
    def read_request(request: Request, record_id: str):
        return audit.detail(principal(request, admin=True)["id"], record_id)

    return router
