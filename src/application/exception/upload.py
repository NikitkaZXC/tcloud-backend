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


class TaskNotPendingError(TCloudError):
    """Raised when a file is sent to a task that is not waiting for one."""

    code = "task_not_pending"


class UploadInProgressError(TCloudError):
    """Raised when another upload to the same path is still running."""

    code = "upload_in_progress"


class TooManyTasksError(TCloudError):
    """Raised when too many uploads are already in progress."""

    code = "too_many_tasks"
