from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Index, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import (
    CreatedAtMixin,
    PrimaryKeyMixin,
    UpdatedAtMixin,
)


class TaskDB(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index(
            "ux_tasks_active_path",
            "path",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
        Index("ix_tasks_status_created", "status", "created_at"),
        Index("ix_tasks_created", "created_at"),
        Index("ix_tasks_batch", "batch_id", "created_at"),
    )

    batch_id: Mapped[UUID | None] = mapped_column(Uuid)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    file_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("files.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    bytes_done: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_total: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[dict | None] = mapped_column(JSONB)
    warnings: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    callback_url: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
