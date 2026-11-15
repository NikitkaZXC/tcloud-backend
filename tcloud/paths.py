"""Пути вида tcloud/{uuid хранилища}/{папки}/{файл} (ТЗ §5)."""

from __future__ import annotations

import unicodedata
import uuid

from .errors import TCloudError

ROOT = "tcloud"
PATH_MAX = 1024
SEGMENT_MAX = 255  # байт


def _bad(message: str) -> TCloudError:
    return TCloudError(422, "invalid_path", message)


def check_segment(s: str) -> None:
    if s in ("", ".", ".."):
        raise _bad("empty, '.' and '..' path segments are not allowed")
    if "\\" in s or any(unicodedata.category(c) == "Cc" for c in s):
        raise _bad("control characters and '\\' are not allowed in paths")
    if len(s.encode()) > SEGMENT_MAX:
        raise _bad(f"path segment is longer than {SEGMENT_MAX} bytes")


def split(raw: str) -> tuple[uuid.UUID, list[str]]:
    """'tcloud/{storage}/a/b/c.jpg' → (storage, ['a', 'b', 'c.jpg'])."""
    raw = unicodedata.normalize("NFC", raw)
    if len(raw) > PATH_MAX:
        raise _bad(f"path is longer than {PATH_MAX} characters")
    parts = raw.split("/")
    if len(parts) < 2 or parts[0] != ROOT:
        raise _bad(f"path must start with {ROOT}/<storage id>")
    try:
        sid = uuid.UUID(parts[1])
    except ValueError:
        raise _bad("invalid storage id in path") from None
    if str(sid) != parts[1].lower():  # только канонический вид, чтобы у файла был ровно один путь
        raise _bad("storage id must be a canonical uuid")
    for s in parts[2:]:
        check_segment(s)
    return sid, parts[2:]


def split_folder(raw: str) -> tuple[uuid.UUID, str]:
    sid, segs = split(raw)
    return sid, "/".join(segs)


def split_file(raw: str) -> tuple[uuid.UUID, str, str]:
    sid, segs = split(raw)
    if not segs:
        raise _bad("file name is missing in path")
    return sid, "/".join(segs[:-1]), segs[-1]


def join(sid: uuid.UUID, folder: str, name: str | None = None) -> str:
    return "/".join(p for p in (ROOT, str(sid), folder, name) if p)
