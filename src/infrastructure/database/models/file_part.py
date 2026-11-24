from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base


class FilePartDB(Base):
    __tablename__ = "file_parts"
    __table_args__ = (Index("ix_file_parts_message", "tg_message_id"),)

    file_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("files.id", ondelete="CASCADE"), primary_key=True
    )
    index: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
