from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from src.api.schemas.base import RequestBody
from src.application.schemas import (
    BatchServiceRead,
    TaskCountsServiceRead,
    TaskListServiceRead,
    TaskServiceRead,
    UploadSessionServiceRead,
)
from src.utilities.formatters import (
    FileSize,
    OnConflict,
    Priority,
    SendAs,
    TcloudPath,
)

BATCH_MAX = 100


class UploadParams(BaseModel):
    path: TcloudPath
    send_as: SendAs | None = None
    on_conflict: OnConflict | None = None
    priority: Priority | None = None
    allow_unscannable: bool | None = None
    encrypt: bool | None = None
    callback_url: str | None = None


class UploadSessionParams(UploadParams):
    size: FileSize


class UploadSessionResponse(UploadSessionServiceRead):
    pass


class UrlUploadCreate(UploadParams, RequestBody):
    url: str
    size: FileSize | None = None


class BatchCreate(RequestBody):
    items: list[UrlUploadCreate] = Field(min_length=1, max_length=BATCH_MAX)


class TaskListParams(BaseModel):
    batch_id: UUID | None = None
    ids: list[UUID] = Field([], max_length=BATCH_MAX)

    @model_validator(mode="after")
    def check_one(self) -> "TaskListParams":
        if bool(self.batch_id) == bool(self.ids):
            raise ValueError("give batch_id or ids")
        return self


class TaskCountsResponse(TaskCountsServiceRead):
    pass


class PriorityUpdate(RequestBody):
    priority: Priority


class TaskResponse(TaskServiceRead):
    pass


class TaskListResponse(TaskListServiceRead):
    pass


class BatchResponse(BatchServiceRead):
    pass
