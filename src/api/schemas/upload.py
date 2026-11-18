from pydantic import BaseModel, Field

from src.application.schemas import (
    BatchServiceRead,
    TaskListServiceRead,
    TaskServiceRead,
)
from src.utilities.formatters import OnConflict, Priority, SendAs, TcloudPath

BATCH_MAX = 100


class UploadParams(BaseModel):
    path: TcloudPath
    send_as: SendAs | None = None
    on_conflict: OnConflict | None = None
    priority: Priority | None = None
    allow_unscannable: bool | None = None
    encrypt: bool | None = None
    callback_url: str | None = None


class UrlUploadCreate(UploadParams):
    url: str


class BatchCreate(BaseModel):
    items: list[UrlUploadCreate] = Field(min_length=1, max_length=BATCH_MAX)


class PriorityUpdate(BaseModel):
    priority: Priority


class TaskResponse(TaskServiceRead):
    pass


class TaskListResponse(TaskListServiceRead):
    pass


class BatchResponse(BatchServiceRead):
    pass
