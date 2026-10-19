"""Version 2: collaboration facts, separate from private conversation storage."""
import time

from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, uid


class TeamSpace(Base):
    __tablename__ = "team_spaces"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str] = mapped_column(ForeignKey("members.id"))
    state: Mapped[str] = mapped_column(String(16), default="active")
    model_mode: Mapped[str] = mapped_column(String(16), default="authorized")
    model_ids: Mapped[list] = mapped_column(JSON, default=list)
    allow_personal: Mapped[bool] = mapped_column(Boolean, default=False)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    description_revision: Mapped[int] = mapped_column(Integer, default=1)
    event_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class SpaceMember(Base):
    __tablename__ = "team_space_members"
    space_id: Mapped[str] = mapped_column(ForeignKey("team_spaces.id"), primary_key=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default="member")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    joined_at: Mapped[float] = mapped_column(Float, default=time.time)


class SpaceInvite(Base):
    __tablename__ = "team_space_invites"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    space_id: Mapped[str] = mapped_column(ForeignKey("team_spaces.id"), index=True)
    recipient_id: Mapped[str] = mapped_column(ForeignKey("members.id"), index=True)
    sender_id: Mapped[str] = mapped_column(ForeignKey("members.id"))
    role: Mapped[str] = mapped_column(String(16), default="member")
    state: Mapped[str] = mapped_column(String(16), default="pending")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    expires_at: Mapped[float] = mapped_column(Float)


class SpacePlan(Base):
    __tablename__ = "team_space_plans"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    space_id: Mapped[str] = mapped_column(ForeignKey("team_spaces.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[str] = mapped_column(ForeignKey("members.id"))
    assignee_id: Mapped[str] = mapped_column(String(32), default="")
    state: Mapped[str] = mapped_column(String(16), default="pending")
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    inputs: Mapped[list] = mapped_column(JSON, default=list)
    outcomes: Mapped[list] = mapped_column(JSON, default=list)
    completion: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class SpaceComment(Base):
    __tablename__ = "team_space_comments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    space_id: Mapped[str] = mapped_column(ForeignKey("team_spaces.id"), index=True)
    target_kind: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[str] = mapped_column(String(32))
    author_id: Mapped[str] = mapped_column(ForeignKey("members.id"))
    body: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class SpaceActivity(Base):
    __tablename__ = "team_space_activity"
    __table_args__ = (UniqueConstraint("space_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    space_id: Mapped[str] = mapped_column(ForeignKey("team_spaces.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(64))
    target_kind: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[str] = mapped_column(String(32))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class SpaceFolder(Base):
    __tablename__ = "team_space_folders"
    __table_args__ = (UniqueConstraint("space_id", "parent_id", "name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    space_id: Mapped[str] = mapped_column(ForeignKey("team_spaces.id"), index=True)
    parent_id: Mapped[str] = mapped_column(String(32), default="")
    name: Mapped[str] = mapped_column(String(200))
    author_id: Mapped[str] = mapped_column(String(32))


class SpaceUpload(Base):
    __tablename__ = "team_space_uploads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    space_id: Mapped[str] = mapped_column(ForeignKey("team_spaces.id"), index=True)
    author_id: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(200))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default="pending")
    generation: Mapped[str] = mapped_column(String(32), default=uid)
    error: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class SpaceAsset(Base):
    __tablename__ = "team_space_assets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    space_id: Mapped[str] = mapped_column(ForeignKey("team_spaces.id"), index=True)
    folder_id: Mapped[str] = mapped_column(String(32), default="")
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    author_id: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    latest: Mapped[int] = mapped_column(Integer, default=1)
    origin_share_id: Mapped[str] = mapped_column(String(32), default="")
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class SpaceAssetRevision(Base):
    __tablename__ = "team_space_asset_revisions"
    __table_args__ = (UniqueConstraint("asset_id", "number"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    space_id: Mapped[str] = mapped_column(ForeignKey("team_spaces.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("team_space_assets.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    upload_id: Mapped[str] = mapped_column(ForeignKey("team_space_uploads.id"), unique=True)
    author_id: Mapped[str] = mapped_column(String(32))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class SpaceShare(Base):
    __tablename__ = "team_space_shares"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    space_id: Mapped[str] = mapped_column(ForeignKey("team_spaces.id"), index=True)
    author_id: Mapped[str] = mapped_column(String(32))
    plan_id: Mapped[str] = mapped_column(String(32), default="")
    title: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(16), default="active")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    latest: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class SpaceSnapshot(Base):
    __tablename__ = "team_space_snapshots"
    __table_args__ = (UniqueConstraint("share_id", "number"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    space_id: Mapped[str] = mapped_column(ForeignKey("team_spaces.id"), index=True)
    share_id: Mapped[str] = mapped_column(ForeignKey("team_space_shares.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text, default="")
    assets: Mapped[list] = mapped_column(JSON, default=list)
    source_state: Mapped[str] = mapped_column(String(24))
    source_ref: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class SpaceOperation(Base):
    __tablename__ = "team_space_operations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(32), index=True)
    space_id: Mapped[str] = mapped_column(String(32), index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


SPACE_TABLES = [table for table in Base.metadata.sorted_tables if table.name.startswith("team_space")]
