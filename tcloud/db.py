"""PostgreSQL: схема из ТЗ §10. Все наши id — UUID, числа — только id Telegram."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    __mapper_args__ = {"eager_defaults": True}  # created_at/updated_at сразу после INSERT


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Storage(Base):
    __tablename__ = "storages"
    id: Mapped[uuid.UUID] = _id()
    tg_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class Folder(Base):
    __tablename__ = "folders"
    id: Mapped[uuid.UUID] = _id()
    storage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("storages.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    __table_args__ = (
        UniqueConstraint("storage_id", "path"),
        Index("ix_folders_prefix", "storage_id", "path", postgresql_ops={"path": "text_pattern_ops"}),
    )


class File(Base):
    __tablename__ = "files"
    id: Mapped[uuid.UUID] = _id()
    storage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("storages.id", ondelete="CASCADE"))
    folder_path: Mapped[str] = mapped_column(Text)  # "" — корень хранилища
    name: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    mime: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)  # photo | video | audio | animation
    sent_as: Mapped[str] = mapped_column(Text)  # media | file
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[float | None] = mapped_column(Float)
    sha256: Mapped[str] = mapped_column(Text)
    tg_message_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    __table_args__ = (
        UniqueConstraint("storage_id", "folder_path", "name"),
        UniqueConstraint("storage_id", "tg_message_id"),
        Index("ix_files_folder", "storage_id", "folder_path", postgresql_ops={"folder_path": "text_pattern_ops"}),
        Index("ix_files_sha256", "sha256"),
    )


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[uuid.UUID] = _id()
    file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("files.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(Text)  # pending | running | done | failed
    stage: Mapped[str] = mapped_column(Text)  # receiving | scanning | uploading_tg | done
    bytes_done: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_total: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[dict | None] = mapped_column(JSONB)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    callback_url: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()
    __table_args__ = (Index("ix_tasks_status", "status"),)


def connect(url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
