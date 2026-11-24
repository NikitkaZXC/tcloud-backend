import re
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from uuid import uuid4

from src.application.exception import FileTooLargeError, SizeMismatchError
from src.config import settings
from src.infrastructure.media import extension

SUFFIX = re.compile(r"[a-z0-9]{1,16}")


def temp_path(name: str = "") -> Path:
    suffix = f".{extension(name)}" if SUFFIX.fullmatch(extension(name)) else ""
    return settings.upload.TMP_DIR / f"{uuid4().hex}{suffix}"


async def write_body(
    body: AsyncIterator[bytes],
    target: Path,
    limit: int,
    progress: Callable[[int], None] | None = None,
) -> int:
    done = 0
    with open(target, "wb") as file:
        target.chmod(0o600)
        async for chunk in body:
            done += len(chunk)
            if done > limit:
                raise FileTooLargeError(
                    f"file is larger than {limit} bytes", limit=limit
                )
            file.write(chunk)
            if progress:
                progress(done)
    return done


async def append_body(
    body: AsyncIterator[bytes],
    target: Path,
    offset: int,
    size: int,
    progress: Callable[[int], None] | None = None,
) -> None:
    with open(target, "r+b") as file:
        file.truncate(offset)
        file.seek(offset)
        try:
            done = 0
            async for chunk in body:
                done += len(chunk)
                if done > size:
                    raise FileTooLargeError(
                        f"chunk is larger than {size} bytes", limit=size
                    )
                file.write(chunk)
                if progress:
                    progress(done)
            if done != size:
                raise SizeMismatchError(f"declared {size} bytes, received {done}")
        except BaseException:
            file.truncate(offset)
            raise


def task_files(task: dict) -> list[Path]:
    prepared = task.get("prepared") or {}
    paths = (
        task["params"].get("file"),
        prepared.get("send_path"),
        prepared.get("thumb"),
    )
    return [Path(item) for item in paths if item]


def remove_task_files(task: dict) -> None:
    for item in task_files(task):
        item.unlink(missing_ok=True)
