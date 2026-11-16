from src.utilities.errors import TCloudError


class FileTooLargeError(TCloudError):
    """Raised when a file is larger than the account limit or the declared size."""

    code = "too_large"


class SizeMismatchError(TCloudError):
    """Raised when the uploaded body is smaller than the declared size."""

    code = "size_mismatch"


class TaskNotFoundError(TCloudError):
    """Raised when an upload task does not exist."""

    code = "task_not_found"


class LengthRequiredError(TCloudError):
    """Raised when a file upload has no Content-Length header."""

    code = "length_required"


class UploadInProgressError(TCloudError):
    """Raised when another upload to the same path is still running."""

    code = "upload_in_progress"


class TooManyTasksError(TCloudError):
    """Raised when too many uploads are already in progress."""

    code = "too_many_tasks"


class TaskCancelledError(TCloudError):
    """Raised inside a worker when the task was cancelled."""

    code = "cancelled"


class TaskFinishedError(TCloudError):
    """Raised when a finished task is cancelled."""

    code = "task_finished"


class TaskNotQueuedError(TCloudError):
    """Raised when the priority of a task that is not waiting in a queue is changed."""

    code = "task_not_queued"


class TaskNotRetryableError(TCloudError):
    """Raised when a task cannot be retried."""

    code = "task_not_retryable"


class ArchiveNotReadyError(TCloudError):
    """Raised when the archive of a task is downloaded before the task is done."""

    code = "archive_not_ready"


class ArchiveExpiredError(TCloudError):
    """Raised when a finished archive was already removed from the server."""

    code = "archive_expired"


class EncryptRequiresFileError(TCloudError):
    """Raised when encryption is requested for a file sent as Telegram media."""

    code = "encrypt_requires_file"
