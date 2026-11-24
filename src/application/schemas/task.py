from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from src.application.schemas.file import FileServiceRead
from src.application.schemas.settings import UploadSettingsServiceRead


class TaskWaitingServiceRead(BaseModel):
    reason: str
    code: str
    params: dict = {}
    seconds: int | None = None


class TaskWarningServiceRead(BaseModel):
    code: str
    params: dict = {}
    message: str


def task_warning(code: str, message: str, **params: object) -> dict:
    return {"code": code, "params": params, "message": message}


SKIPPED = task_warning("skipped_exists", "skipped: file already exists")


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
    step: str | None = None
    step_progress: float | None = None
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
    warning_details: list[TaskWarningServiceRead]
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


class TaskCountsServiceRead(BaseModel):
    total: int
    by_status: dict[str, int]


class TaskListServiceRead(BaseModel):
    items: list[TaskServiceRead]
    summary: TaskSummaryServiceRead


class UploadSessionServiceRead(BaseModel):
    session_id: UUID
    user_id: UUID
    path: str
    size: int
    offset: int
    chunk_size: int
    expires_at: datetime


class UploadServiceCreate(BaseModel):
    path: str
    size: int | None = None
    url: str | None = None
    send_as: str | None = None
    on_conflict: str | None = None
    priority: str | None = None
    allow_unscannable: bool | None = None
    encrypt: bool | None = None
    callback_url: str | None = None

    def with_defaults(
        self, defaults: UploadSettingsServiceRead
    ) -> "UploadServiceCreate":
        send_as = self.send_as or defaults.send_as
        return self.model_copy(
            update={
                "send_as": send_as,
                "on_conflict": self.on_conflict or defaults.on_conflict,
                "priority": self.priority or defaults.priority,
                "allow_unscannable": (
                    defaults.allow_unscannable
                    if self.allow_unscannable is None
                    else self.allow_unscannable
                ),
                "encrypt": (
                    defaults.encrypt and send_as == "file"
                    if self.encrypt is None
                    else self.encrypt
                ),
                "callback_url": (
                    defaults.webhook_url
                    if self.callback_url is None
                    else self.callback_url
                ),
            }
        )


class BatchItemServiceRead(BaseModel):
    path: str
    task: TaskServiceRead | None
    error: dict | None


class BatchServiceRead(BaseModel):
    batch_id: UUID
    items: list[BatchItemServiceRead]
