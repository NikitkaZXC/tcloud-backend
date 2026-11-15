from datetime import datetime
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
            created_at=file.created_at,
            updated_at=file.updated_at,
            telegram=TelegramFileServiceRead(
                chat_id=chat_id,
                message_id=file.tg_message_id,
                link=telegram_link(chat_id, file.tg_message_id),
                search=caption_token(file.id),
            ),
        )
