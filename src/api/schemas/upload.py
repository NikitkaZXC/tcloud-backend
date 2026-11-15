from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.application.schemas import (
    BatchServiceRead,
    TaskListServiceRead,
    TaskServiceRead,
)
from src.utilities.formatters import FileSize, OnConflict, SendAs, TcloudPath

BATCH_MAX = 100
TaskStatusFilter = Literal["pending", "running", "done", "failed"]


class UploadCreate(BaseModel):
    path: TcloudPath
    size: FileSize
    send_as: SendAs = "file"
    on_conflict: OnConflict = "error"
    callback_url: str | None = None


class UrlUploadCreate(BaseModel):
    url: str
    path: TcloudPath
    send_as: SendAs = "file"
    on_conflict: OnConflict = "error"
    callback_url: str | None = None


class BatchItemCreate(BaseModel):
    path: TcloudPath
    size: FileSize | None = None
    url: str | None = None
    send_as: SendAs = "file"
    on_conflict: OnConflict = "error"
    callback_url: str | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "BatchItemCreate":
        if (self.size is None) == (self.url is None):
            raise ValueError("set exactly one of size (upload from device) or url")
        return self


class BatchCreate(BaseModel):
    items: list[BatchItemCreate] = Field(min_length=1, max_length=BATCH_MAX)


class TaskResponse(TaskServiceRead):
    pass


class TaskListResponse(TaskListServiceRead):
    pass


class BatchResponse(BatchServiceRead):
    pass
