from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.application.schemas.file import FileServiceRead
from src.infrastructure.database.models import FolderDB
from src.utilities.parsers import join_path


class FolderServiceRead(BaseModel):
    id: UUID
    path: str
    name: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_db(cls, folder: FolderDB) -> "FolderServiceRead":
        return cls(
            id=folder.id,
            path=join_path(folder.storage_id, folder.path),
            name=folder.path.rpartition("/")[2],
            created_at=folder.created_at,
            updated_at=folder.updated_at,
        )


class FolderSizeServiceRead(FolderServiceRead):
    size: int
    files: int


class FolderEntryServiceRead(FolderSizeServiceRead):
    type: Literal["folder"] = "folder"


class FileEntryServiceRead(FileServiceRead):
    type: Literal["file"] = "file"


class FolderListingServiceCreate(BaseModel):
    sort_by: Literal["name", "modified", "uploaded", "type", "size"] | None = None
    sort_order: Literal["asc", "desc"] | None = None
    folders_first: bool | None = None
    lang: Literal["ru", "en"] | None = None
    limit: int = 20
    cursor: str | None = None


class FolderListingServiceRead(BaseModel):
    path: str
    size: int
    total_files: int
    total_folders: int
    direct_files: int
    items: list[
        Annotated[
            FolderEntryServiceRead | FileEntryServiceRead, Field(discriminator="type")
        ]
    ]
    next_cursor: str | None


class FolderDeleteServiceRead(BaseModel):
    path: str
    deleted_files: int
