from fastapi import status

STATUS_BY_CODE = {
    "invalid_path": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "bad_range": status.HTTP_416_RANGE_NOT_SATISFIABLE,
    "unsupported_media_type": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    "invalid_media": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "extension_mismatch": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "infected": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "scanner_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "unsafe_url": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "download_failed": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "too_large": status.HTTP_413_CONTENT_TOO_LARGE,
    "insufficient_storage": status.HTTP_507_INSUFFICIENT_STORAGE,
    "telegram_error": status.HTTP_502_BAD_GATEWAY,
    "telegram_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "flood_wait": status.HTTP_429_TOO_MANY_REQUESTS,
    "channel_unusable": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "storage_not_found": status.HTTP_404_NOT_FOUND,
    "photo_not_found": status.HTTP_404_NOT_FOUND,
    "folder_not_found": status.HTTP_404_NOT_FOUND,
    "file_not_found": status.HTTP_404_NOT_FOUND,
    "task_not_found": status.HTTP_404_NOT_FOUND,
    "conflict": status.HTTP_409_CONFLICT,
    "length_required": status.HTTP_411_LENGTH_REQUIRED,
    "queue_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "upload_in_progress": status.HTTP_409_CONFLICT,
    "task_finished": status.HTTP_409_CONFLICT,
    "task_not_queued": status.HTTP_409_CONFLICT,
    "task_not_retryable": status.HTTP_409_CONFLICT,
    "too_many_tasks": status.HTTP_429_TOO_MANY_REQUESTS,
    "size_mismatch": status.HTTP_400_BAD_REQUEST,
}


def status_for(code: str) -> int:
    return STATUS_BY_CODE.get(code, status.HTTP_500_INTERNAL_SERVER_ERROR)
