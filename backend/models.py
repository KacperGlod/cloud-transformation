from datetime import datetime, timezone
from pathlib import Path
import uuid

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LoginSession(Base):
    __tablename__ = "login_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Cloud(Base):
    __tablename__ = "clouds"
    __table_args__ = (
        CheckConstraint("visibility IN ('public', 'private')", name="ck_cloud_visibility"),
        Index("ix_cloud_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    visibility: Mapped[str] = mapped_column(String(7), default="private", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="uploading", nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    source_file: Mapped[str | None] = mapped_column(String(255))
    processing_file: Mapped[str | None] = mapped_column(String(255))
    entry_file: Mapped[str | None] = mapped_column(String(40))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clouds.id", ondelete="SET NULL"))
    operation: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def processing_filename(self) -> str | None:
        if self.processing_file:
            return self.processing_file
        if self.source_file and Path(self.source_file).suffix.lower() in (".las", ".laz"):
            return self.source_file
        return None


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_project_revision"),
        Index("ix_project_owner_updated", "owner_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    cloud_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clouds.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    scene_state: Mapped[dict] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (Index("ix_job_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cloud_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clouds.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    source_cloud_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clouds.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(24), default="import")
    status: Mapped[str] = mapped_column(String(24), default="uploading")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    upload_progress: Mapped[int | None] = mapped_column(Integer, default=0)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    log: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
