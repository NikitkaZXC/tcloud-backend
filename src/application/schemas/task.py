from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from src.application.schemas.file import FileServiceRead


class TaskWaitingServiceRead(BaseModel):
    reason: str
    seconds: int | None = None


class TaskArchiveServiceRead(BaseModel):
    name: str
    format: str
    files: int
    size: int | None = None
    expires_at: datetime | None = None


class TaskServiceRead(BaseModel):
    id: UUID
    batch_id: UUID | None
    user_id: UUID | None = None
    status: str
    stage: str
    priority: str
    path: str
    bytes_done: int
    bytes_total: int | None
    progress: float | None
    speed: int | None
    eta: float | None
    queue_position: int | None
    waiting: TaskWaitingServiceRead | None
    cancel_requested: bool
    error: dict | None
    warnings: list[str]
    file: FileServiceRead | None
    archive: TaskArchiveServiceRead | None = None
    created_at: datetime
    updated_at: datetime


class TaskSummaryServiceRead(BaseModel):
    total: int
    by_status: dict[str, int]
    bytes_done: int
    bytes_total: int
    finished: int
    progress: float | None


class TaskListServiceRead(BaseModel):
    items: list[TaskServiceRead]
    summary: TaskSummaryServiceRead


class UploadServiceCreate(BaseModel):
    path: str
    size: int | None = None
    url: str | None = None
    send_as: str = "file"
    on_conflict: str = "error"
    priority: str = "normal"
    allow_unscannable: bool = False
    encrypt: bool = False
    callback_url: str | None = None


class BatchItemServiceRead(BaseModel):
    path: str
    task: TaskServiceRead | None
    error: dict | None


class BatchServiceRead(BaseModel):
    batch_id: UUID
    items: list[BatchItemServiceRead]
