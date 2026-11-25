import re
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from uuid import UUID, uuid4

from loguru import logger

from src.application.exception import FileTooLargeError, SizeMismatchError
from src.application.services.webhook import notify
from src.config import settings
from src.infrastructure.cache import TaskStore
from src.infrastructure.database.session import session_factory
from src.infrastructure.enums import TaskStatus
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


async def used_files(store: TaskStore) -> set[Path]:
    tasks = await store.get_many(await store.active_ids())
    used = {path for task in tasks for path in task_files(task)}
    used.update(Path(archive) for archive in await store.archives())
    used.update(
        Path(session["task"]["params"]["file"])
        for _, _, session in await store.sessions()
        if session
    )
    return used


def remove_task_files(task: dict) -> None:
    for item in task_files(task):
        item.unlink(missing_ok=True)


async def drop_session(store: TaskStore, session: dict) -> None:
    task = session["task"]
    await store.drop_session(task["id"])
    Path(task["params"]["file"]).unlink(missing_ok=True)
    await store.release(task["id"], task["path"])


async def cancel_user_sessions(store: TaskStore, user_id: UUID) -> int:
    cancelled = 0
    for session_id, _, session in await store.sessions():
        if not session or session["task"]["user_id"] != str(user_id):
            continue
        async with store.holding(f"upload:{session_id}") as held:
            session = await store.get_session(session_id)
            if held and session:
                await drop_session(store, session)
                cancelled += 1
    if cancelled:
        logger.info("{} upload sessions of user {} cancelled", cancelled, user_id)
    return cancelled


async def cancel_task(
    store: TaskStore, task_id: UUID | str, spawn: Callable[[Coroutine], object]
) -> str:
    result = await store.cancel(task_id)
    if result == "cancelled":
        task = await store.get(task_id)
        await store.release(task_id, task["path"])
        remove_task_files(task)
        if task["params"].get("callback_url"):
            notify(
                spawn, store, session_factory, task_id, task["params"]["callback_url"]
            )
    return result


async def cancel_user_tasks(
    store: TaskStore, user_id: UUID, spawn: Callable[[Coroutine], object]
) -> int:
    tasks = await store.get_many(await store.user_task_ids(user_id), ("status",))
    cancelled = 0
    for task in tasks:
        if task["status"] not in TaskStatus.finished():
            await cancel_task(store, task["id"], spawn)
            cancelled += 1
    if cancelled:
        logger.info("{} tasks of user {} cancelled", cancelled, user_id)
    return cancelled
