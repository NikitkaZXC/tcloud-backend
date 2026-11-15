from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.base import Base
from src.infrastructure.database.mixins import (
    CreatedAtMixin,
    PrimaryKeyMixin,
    UpdatedAtMixin,
)


class StorageDB(PrimaryKeyMixin, CreatedAtMixin, UpdatedAtMixin, Base):
    __tablename__ = "storages"

    tg_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
