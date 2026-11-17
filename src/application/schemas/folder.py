from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

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


class FolderListingServiceRead(BaseModel):
    path: str
    size: int
    total_files: int
    folders: list[FolderSizeServiceRead]
    files: list[FileServiceRead]


class FolderDeleteServiceRead(BaseModel):
    path: str
    deleted_files: int
