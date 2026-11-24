import unicodedata
from uuid import UUID

from src.utilities.parsers.exception import InvalidPathError

ROOT = "tcloud"
PATH_MAX = 1024
SEGMENT_MAX = 255


def check_segment(segment: str) -> None:
    if segment in ("", ".", ".."):
        raise InvalidPathError("empty, '.' and '..' path segments are not allowed")
    if "\\" in segment or any(
        unicodedata.category(char) in ("Cc", "Cf") for char in segment
    ):
        raise InvalidPathError(
            "control and format characters and '\\' are not allowed in paths"
        )
    if len(segment.encode()) > SEGMENT_MAX:
        raise InvalidPathError(f"path segment is longer than {SEGMENT_MAX} bytes")


def split_path(raw: str) -> tuple[UUID, list[str]]:
    raw = unicodedata.normalize("NFC", raw)
    if len(raw) > PATH_MAX:
        raise InvalidPathError(f"path is longer than {PATH_MAX} characters")
    parts = raw.split("/")
    if len(parts) < 2 or parts[0] != ROOT:
        raise InvalidPathError(f"path must start with {ROOT}/<storage id>")
    try:
        storage_id = UUID(parts[1])
    except ValueError:
        raise InvalidPathError("invalid storage id in path") from None
    if str(storage_id) != parts[1].lower():
        raise InvalidPathError("storage id must be a canonical uuid")
    for segment in parts[2:]:
        check_segment(segment)
    return storage_id, parts[2:]


def split_folder(raw: str) -> tuple[UUID, str]:
    storage_id, segments = split_path(raw)
    return storage_id, "/".join(segments)


def split_file(raw: str) -> tuple[UUID, str, str]:
    storage_id, segments = split_path(raw)
    if not segments:
        raise InvalidPathError("file name is missing in path")
    return storage_id, "/".join(segments[:-1]), segments[-1]


def join_path(storage_id: UUID, folder: str, name: str | None = None) -> str:
    return "/".join(part for part in (ROOT, str(storage_id), folder, name) if part)
