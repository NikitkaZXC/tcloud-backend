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
    send_as: SendAs = "file"
    on_conflict: OnConflict = "error"
    priority: Priority = "normal"
    callback_url: str | None = None


class UrlUploadCreate(UploadParams):
    url: str


class BatchCreate(BaseModel):
    items: list[UrlUploadCreate] = Field(min_length=1, max_length=BATCH_MAX)


class TaskResponse(TaskServiceRead):
    pass


class TaskListResponse(TaskListServiceRead):
    pass


class BatchResponse(BatchServiceRead):
    pass
