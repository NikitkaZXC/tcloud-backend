from uuid import UUID

from pydantic import BaseModel, Field

from src.application.schemas import (
    FolderDeleteServiceRead,
    FolderListingServiceRead,
    FolderServiceRead,
)
from src.infrastructure.enums import ArchiveFormat
from src.utilities.formatters import ArchiveName, Priority, TcloudPath


class FolderCreate(BaseModel):
    path: TcloudPath


class FolderResponse(FolderServiceRead):
    pass


class FolderListingResponse(FolderListingServiceRead):
    pass


class FolderDeleteResponse(FolderDeleteServiceRead):
    pass


class SelectionArchiveCreate(BaseModel):
    files: list[UUID] = Field(default_factory=list)
    folders: list[TcloudPath] = Field(default_factory=list)
    format: ArchiveFormat = ArchiveFormat.ZIP
    name: ArchiveName | None = None
    priority: Priority = "normal"
