from collections.abc import AsyncIterator, Callable
from pathlib import Path
from uuid import uuid4

from src.application.exception import FileTooLargeError
from src.config import settings
from src.infrastructure.media import extension


def temp_path(name: str = "") -> Path:
    suffix = f".{extension(name)}" if extension(name) else ""
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
