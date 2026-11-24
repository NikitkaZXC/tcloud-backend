from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.api.schemas.base import RequestBody
from src.application.schemas import (
    FolderDeleteServiceRead,
    FolderListingServiceRead,
    FolderServiceRead,
)
from src.infrastructure.enums import ArchiveFormat
from src.utilities.formatters import ArchiveName, Priority, TcloudPath


class FolderCreate(RequestBody):
    path: TcloudPath


class FolderResponse(FolderServiceRead):
    pass


LIST_LIMIT_MAX = 200


class FolderListParams(BaseModel):
    path: str
    sort_by: Literal["name", "modified", "uploaded", "type", "size"] | None = None
    sort_order: Literal["asc", "desc"] | None = None
    folders_first: bool | None = None
    lang: Literal["ru", "en"] | None = None
    limit: int = Field(20, ge=1, le=LIST_LIMIT_MAX)
    cursor: str | None = Field(None, max_length=4096)


class FolderListingResponse(FolderListingServiceRead):
    pass


class FolderDeleteResponse(FolderDeleteServiceRead):
    pass


class SelectionArchiveCreate(RequestBody):
    files: list[UUID] = Field(default_factory=list)
    folders: list[TcloudPath] = Field(default_factory=list)
    format: ArchiveFormat | None = None
    name: ArchiveName | None = None
    priority: Priority | None = None
