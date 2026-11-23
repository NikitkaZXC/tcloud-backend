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
    func,
    text,
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
        Index(
            "ix_files_name_trgm",
            func.lower(text("name")).label("name_lower"),
            postgresql_using="gin",
            postgresql_ops={"name_lower": "gin_trgm_ops"},
        ),
        Index("ix_files_name", "name"),
        Index("ix_files_name_ru", text('name COLLATE "tcloud_ru"')),
        Index("ix_files_name_en", text('name COLLATE "tcloud_en"')),
        *(
            Index(
                f"ix_files_list_name_{lang}",
                "storage_id",
                "folder_path",
                text(f'name COLLATE "tcloud_{lang}"'),
                "id",
            )
            for lang in ("ru", "en")
        ),
        *(
            Index(
                f"ix_files_list_type_{lang}",
                "storage_id",
                "folder_path",
                text(
                    "(coalesce(nullif(lower(substring(name, '\\.([^.]*)$')), ''), kind))"
                    f' COLLATE "tcloud_{lang}"'
                ),
                text(f'name COLLATE "tcloud_{lang}"'),
                "id",
            )
            for lang in ("ru", "en")
        ),
        Index("ix_files_list_size", "storage_id", "folder_path", "size", "id"),
        Index("ix_files_list_updated", "storage_id", "folder_path", "updated_at", "id"),
        Index("ix_files_list_created", "storage_id", "folder_path", "created_at", "id"),
        Index("ix_files_kind", "kind"),
        Index("ix_files_mime", "mime"),
        Index("ix_files_size", "size"),
        Index("ix_files_updated_at", "updated_at"),
        Index("ix_files_created_at", "created_at"),
        Index(
            "ix_files_encrypted",
            "storage_id",
            postgresql_where=text("encryption_header IS NOT NULL"),
        ),
        Index(
            "ix_files_preview",
            "id",
            postgresql_where=text("thumbnail IS NOT NULL"),
        ),
        Index(
            "ix_files_missing_preview",
            "kind",
            "id",
            postgresql_where=text(
                "encryption_header IS NOT NULL AND thumbnail IS NULL"
            ),
        ),
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
    thumbnail: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
