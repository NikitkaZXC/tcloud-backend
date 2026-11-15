from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    @classmethod
    def active(cls) -> tuple["TaskStatus", ...]:
        return cls.PENDING, cls.RUNNING


class TaskStage(StrEnum):
    RECEIVING = "receiving"
    SCANNING = "scanning"
    UPLOADING_TG = "uploading_tg"
    DONE = "done"


class TaskSource(StrEnum):
    DEVICE = "device"
    URL = "url"
