from pydantic import BaseModel

from src.application.schemas import (
    FolderDeleteServiceRead,
    FolderListingServiceRead,
    FolderServiceRead,
)
from src.utilities.formatters import TcloudPath


class FolderCreate(BaseModel):
    path: TcloudPath


class FolderResponse(FolderServiceRead):
    pass


class FolderListingResponse(FolderListingServiceRead):
    pass


class FolderDeleteResponse(FolderDeleteServiceRead):
    pass
