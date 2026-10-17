"""Short SQL transactions; model streams never hold database connections."""
from __future__ import annotations

from contextlib import contextmanager
import time
import uuid

from sqlalchemy import Boolean, Float, Integer, JSON, String, Text, UniqueConstraint, create_engine, select, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def uid():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Platform(Base):
    __tablename__ = "platform"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    platform_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(80))
    public_url: Mapped[str] = mapped_column(Text)
    config_version: Mapped[int] = mapped_column(default=1)
    catalog_revision: Mapped[int] = mapped_column(default=0)
    schema_version: Mapped[int] = mapped_column(default=1)


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (UniqueConstraint("provider_id", "subject"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    provider_id: Mapped[str] = mapped_column(String(128))
    subject: Mapped[str] = mapped_column(String(512))
    name: Mapped[str] = mapped_column(String(256))
    email: Mapped[str] = mapped_column(String(512), default="")
    role: Mapped[str] = mapped_column(String(16), default="member")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(default=1)
    default_model_id: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    last_login_at: Mapped[float] = mapped_column(Float, default=time.time)


class LocalAdministrator(Base):
    __tablename__ = "local_administrator"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    member_id: Mapped[str] = mapped_column(String(32), unique=True)
    username: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(Text)
    salt: Mapped[str] = mapped_column(String(64))


class Installation(Base):
    __tablename__ = "installation"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    failures: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[float] = mapped_column(Float, default=0)


class AuthSource(Base):
    __tablename__ = "auth_sources"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(16))
    config: Mapped[dict] = mapped_column(JSON)
    secret: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(default=1)
    verified_revision: Mapped[int] = mapped_column(default=0)


class LoginTransaction(Base):
    __tablename__ = "login_transactions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    provider_id: Mapped[str] = mapped_column(String(128))
    source_snapshot: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(16))
    challenge: Mapped[str] = mapped_column(String(128), default="")
    browser_hash: Mapped[str] = mapped_column(String(64), default="")
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    transient_secret: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    member_id: Mapped[str] = mapped_column(String(32), default="")
    error: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    csrf_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class Model(Base):
    __tablename__ = "models"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(16))
    protocol: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(256))
    base_url: Mapped[str] = mapped_column(Text)
    secret: Mapped[str] = mapped_column(Text)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(default=1)
    tested_revision: Mapped[int] = mapped_column(default=0)


class ModelGrant(Base):
    __tablename__ = "model_grants"
    member_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(32), primary_key=True)


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(32), index=True)
    client_id: Mapped[str] = mapped_column(String(64))
    submission_id: Mapped[str] = mapped_column(String(128))
    model_id: Mapped[str] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)
    received_at: Mapped[float] = mapped_column(Float, index=True)
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class ClientRelease(Base):
    __tablename__ = "client_releases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    state: Mapped[str] = mapped_column(String(24), default="draft")
    stage: Mapped[str] = mapped_column(String(24), default="")
    revision: Mapped[int] = mapped_column(default=1)
    generation: Mapped[str] = mapped_column(String(32), default=uid)
    app_version: Mapped[str] = mapped_column(String(32))
    target_os: Mapped[str] = mapped_column(String(16), default="windows")
    arch: Mapped[str] = mapped_column(String(16), default="x64")
    notes: Mapped[str] = mapped_column(Text, default="")
    source_sha256: Mapped[str] = mapped_column(String(64), default="")
    source_bytes: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    output_sha256: Mapped[str] = mapped_column(String(64), default="")
    output_bytes: Mapped[int] = mapped_column(Integer, default=0)
    recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    published_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class Operation(Base):
    __tablename__ = "operations"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(32))
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    member_id: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(128), default="")
    outcome: Mapped[str] = mapped_column(String(80), default="ok")
    created_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)


class Database:
    def __init__(self, settings):
        options = {"pool_pre_ping": True, "hide_parameters": True}
        if settings.database_url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False}
            if ":memory:" in settings.database_url:
                options["poolclass"] = StaticPool
        else:
            options.update(pool_size=10, max_overflow=10, pool_timeout=15)
        self.engine = create_engine(settings.database_url, **options)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        if self.engine.dialect.name == "sqlite":
            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=15000")

    @contextmanager
    def transaction(self):
        with self.sessions.begin() as session:
            yield session

    def migrate(self, settings):
        Base.metadata.create_all(self.engine)
        with self.transaction() as db:
            platform = db.get(Platform, 1)
            if platform is None:
                db.add(Platform(id=1, platform_id=settings.platform_id,
                                name=settings.platform_name, public_url=settings.public_url))
            elif platform.platform_id != settings.platform_id:
                raise ValueError("数据库属于其他 platform_id；不得复用为新企业实例。")
            elif platform.schema_version != 1:
                raise ValueError("数据库结构版本不兼容，需执行对应版本迁移。")
            elif (platform.name, platform.public_url) != (settings.platform_name, settings.public_url):
                platform.name, platform.public_url = settings.platform_name, settings.public_url
                platform.config_version += 1

    def audit(self, db, action, *, member_id="", target="", outcome="ok"):
        db.add(AuditEvent(action=action, member_id=member_id, target=target, outcome=outcome))

    def close(self):
        self.engine.dispose()
