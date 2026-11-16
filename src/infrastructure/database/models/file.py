from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import (
    CreatedAtMixin,
    PrimaryKeyMixin,
    UpdatedAtMixin,
)


class FileDB(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("storage_id", "folder_path", "name"),
        UniqueConstraint("storage_id", "tg_message_id"),
        Index(
            "ix_files_folder",
            "storage_id",
            "folder_path",
            postgresql_ops={"folder_path": "text_pattern_ops"},
        ),
        Index("ix_files_sha256", "sha256"),
    )

    storage_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("storages.id", ondelete="CASCADE"), nullable=False
    )
    folder_path: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    sent_as: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[float | None] = mapped_column(Float)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    tg_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    encryption_header: Mapped[bytes | None] = mapped_column(LargeBinary)
