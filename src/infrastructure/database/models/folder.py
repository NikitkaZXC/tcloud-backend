from uuid import UUID

from sqlalchemy import ForeignKey, Index, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import (
    CreatedAtMixin,
    PrimaryKeyMixin,
    UpdatedAtMixin,
)


class FolderDB(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "folders"
    __table_args__ = (
        UniqueConstraint("storage_id", "path"),
        Index(
            "ix_folders_prefix",
            "storage_id",
            "path",
            postgresql_ops={"path": "text_pattern_ops"},
        ),
        Index(
            "ix_folders_path_trgm",
            func.lower(text("path")).label("path_lower"),
            postgresql_using="gin",
            postgresql_ops={"path_lower": "gin_trgm_ops"},
        ),
    )

    storage_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("storages.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
