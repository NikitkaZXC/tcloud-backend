from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from src.infrastructure.database.models import StorageDB
from src.infrastructure.integrations import ChannelInfo
from src.utilities.parsers import join_path


class TelegramStorageServiceRead(BaseModel):
    chat_id: int


class StorageServiceRead(BaseModel):
    id: UUID
    path: str
    name: str | None
    description: str | None
    has_photo: bool
    available: bool
    telegram: TelegramStorageServiceRead
    created_at: datetime
    updated_at: datetime
    files: int | None = None
    total_size: int | None = None
    preview_count: int
    preview_bytes: int

    @classmethod
    def from_db(
        cls,
        storage: StorageDB,
        info: ChannelInfo | None,
        files: int,
        total_size: int,
        preview_count: int,
        preview_bytes: int,
    ) -> "StorageServiceRead":
        return cls(
            id=storage.id,
            path=join_path(storage.id, ""),
            name=info.title if info else None,
            description=info.about if info else None,
            has_photo=bool(info and info.has_photo),
            available=info is not None,
            telegram=TelegramStorageServiceRead(chat_id=storage.tg_chat_id),
            created_at=storage.created_at,
            updated_at=storage.updated_at,
            files=files,
            total_size=total_size,
            preview_count=preview_count,
            preview_bytes=preview_bytes,
        )


class StorageServiceCreate(BaseModel):
    name: str
    description: str | None = None


class StorageServiceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class StorageActivityServiceRead(BaseModel):
    storage_id: UUID
    read: bool
    write: bool
