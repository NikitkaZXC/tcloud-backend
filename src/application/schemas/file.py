from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel

from src.infrastructure.database.models import FileDB
from src.utilities.parsers import join_path


def caption_token(file_id: UUID) -> str:
    return f"#tc{file_id.hex}"


def telegram_link(chat_id: int, message_id: int) -> str:
    return f"https://t.me/c/{str(chat_id).removeprefix('-100')}/{message_id}"


class TelegramFileServiceRead(BaseModel):
    chat_id: int
    message_id: int
    link: str
    search: str


class FileServiceRead(BaseModel):
    uuid: UUID
    path: str
    storage_id: UUID
    name: str
    size: int
    mime: str
    kind: str
    sent_as: str
    width: int | None
    height: int | None
    duration: float | None
    sha256: str
    encrypted: bool
    created_at: datetime
    updated_at: datetime
    telegram: TelegramFileServiceRead

    @classmethod
    def from_db(cls, file: FileDB, chat_id: int) -> "FileServiceRead":
        return cls(
            uuid=file.id,
            path=join_path(file.storage_id, file.folder_path, file.name),
            storage_id=file.storage_id,
            name=file.name,
            size=file.size,
            mime=file.mime,
            kind=file.kind,
            sent_as=file.sent_as,
            width=file.width,
            height=file.height,
            duration=file.duration,
            sha256=file.sha256,
            encrypted=file.encryption_header is not None,
            created_at=file.created_at,
            updated_at=file.updated_at,
            telegram=TelegramFileServiceRead(
                chat_id=chat_id,
                message_id=file.tg_message_id,
                link=telegram_link(chat_id, file.tg_message_id),
                search=caption_token(file.id),
            ),
        )


class FileSearchServiceCreate(BaseModel):
    q: str | None = None
    path: str | None = None
    kind: list[str] = []
    mime: list[str] = []
    size_min: int | None = None
    size_max: int | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    encrypted: bool | None = None
    sort: str = "relevance"
    order: str | None = None
    limit: int = 50
    offset: int = 0


class FoundFileServiceRead(FileServiceRead):
    folder: str
    storage_name: str | None


class FoundFolderServiceRead(BaseModel):
    id: UUID
    path: str
    name: str
    storage_id: UUID
    storage_name: str | None
    created_at: datetime
    updated_at: datetime


class FileSearchServiceRead(BaseModel):
    items: list[FoundFileServiceRead]
    total: int
    limit: int
    offset: int
    has_more: bool
    folders: list[FoundFolderServiceRead]


class MimeCountServiceRead(BaseModel):
    mime: str
    files: int


class ActivityDayServiceRead(BaseModel):
    date: date
    files: int
    size: int


class ActivityServiceRead(BaseModel):
    since: date
    until: date
    days: int
    files: int
    size: int
    items: list[ActivityDayServiceRead]
