from enum import StrEnum


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def finished(cls) -> tuple["TaskStatus", ...]:
        return cls.DONE, cls.FAILED, cls.CANCELLED


class TaskStage(StrEnum):
    DOWNLOADING = "downloading"
    SCANNING = "scanning"
    UPLOADING_TG = "uploading_tg"
    DONE = "done"


class TaskSource(StrEnum):
    DEVICE = "device"
    URL = "url"


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"

    @property
    def level(self) -> int:
        return {TaskPriority.LOW: 1, TaskPriority.NORMAL: 5, TaskPriority.HIGH: 9}[self]


class UploadQueue(StrEnum):
    DOWNLOAD = "uploads.download"
    PREPARE = "uploads.prepare"
    TELEGRAM = "uploads.telegram"
