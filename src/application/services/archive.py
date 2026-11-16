import asyncio
from collections.abc import AsyncIterator
from typing import BinaryIO

from loguru import logger

from src.application.services.temp import temp_path, write_body
from src.infrastructure.archive import CHUNK, ArchiveWriter
from src.infrastructure.background.disk import DiskBudget
from src.infrastructure.database.models import FileDB
from src.infrastructure.enums import ArchiveFormat
from src.infrastructure.integrations import TelegramClient, TelegramMessageGoneError

POLL = 0.2


def entry_name(base: str, file: FileDB) -> str:
    inner = (
        file.folder_path.removeprefix(base).lstrip("/") if base else file.folder_path
    )
    return f"{inner}/{file.name}" if inner else file.name


async def build_archive(
    download: TelegramClient,
    chat_id: int,
    files: list[FileDB],
    base: str,
    archive_format: ArchiveFormat,
    target: BinaryIO,
) -> None:
    writer = ArchiveWriter(archive_format, target)
    for file in files:
        part = temp_path(file.name)
        try:
            try:
                media, size = await download.message_media(chat_id, file.tg_message_id)
            except TelegramMessageGoneError:
                logger.warning("File {} is gone from Telegram, not archived", file.id)
                continue
            if size != file.size:
                logger.warning("File {} changed in Telegram, not archived", file.id)
                continue
            await write_body(download.iter_media(media, 0, size), part, size)
            await asyncio.to_thread(
                writer.add, part, entry_name(base, file), file.updated_at.timestamp()
            )
        finally:
            part.unlink(missing_ok=True)
    await asyncio.to_thread(writer.close)


async def stream_archive(
    download: TelegramClient,
    chat_id: int,
    files: list[FileDB],
    base: str,
    archive_format: ArchiveFormat,
    disk: DiskBudget,
    size: int,
) -> AsyncIterator[bytes]:
    target = temp_path()
    reservation = disk.reserve(size)
    try:
        with target.open("wb") as output, target.open("rb") as source:
            target.chmod(0o600)
            builder = asyncio.create_task(
                build_archive(download, chat_id, files, base, archive_format, output)
            )
            try:
                while True:
                    done = builder.done()
                    chunk = await asyncio.to_thread(source.read, CHUNK)
                    if chunk:
                        yield chunk
                    elif done:
                        builder.result()
                        return
                    else:
                        await asyncio.sleep(POLL)
            finally:
                builder.cancel()
    finally:
        target.unlink(missing_ok=True)
        reservation.release()
