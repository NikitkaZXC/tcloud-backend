from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import (
    CreatedAtMixin,
    PrimaryKeyMixin,
    UpdatedAtMixin,
)


class FolderGrantDB(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "folder_grants"
    __table_args__ = (UniqueConstraint("user_id", "storage_id", "path"),)

    user_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    storage_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("storages.id", ondelete="CASCADE"), index=True, nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    recursive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    can_read: Mapped[bool] = mapped_column(Boolean, nullable=False)
    can_upload: Mapped[bool] = mapped_column(Boolean, nullable=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, nullable=False)
