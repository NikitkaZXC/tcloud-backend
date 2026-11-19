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
    ARCHIVING = "archiving"
    DONE = "done"


class TaskStep(StrEnum):
    QUEUED_FOR_DOWNLOAD = "queued_for_download"
    QUEUED_FOR_PREPARE = "queued_for_prepare"
    QUEUED_FOR_TELEGRAM = "queued_for_telegram"
    QUEUED_FOR_ARCHIVE = "queued_for_archive"
    DOWNLOADING = "downloading"
    ANALYZING = "analyzing"
    SCANNING = "scanning"
    CONVERTING = "converting"
    ENCRYPTING = "encrypting"
    PREVIEWING = "previewing"
    SENDING = "sending"
    FINISHING = "finishing"
    ARCHIVING = "archiving"

    @classmethod
    def measured(cls) -> tuple["TaskStep", ...]:
        return (
            cls.DOWNLOADING,
            cls.ANALYZING,
            cls.SCANNING,
            cls.ENCRYPTING,
            cls.SENDING,
            cls.ARCHIVING,
        )


class TaskSource(StrEnum):
    DEVICE = "device"
    URL = "url"
    ARCHIVE = "archive"


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
    ARCHIVE = "archives.build"

    @property
    def step(self) -> TaskStep:
        return TaskStep(f"queued_for_{self.name.lower()}")
