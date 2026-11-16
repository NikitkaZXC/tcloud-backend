import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger

from src.application.services.file import file_body, stored_size
from src.application.services.temp import temp_path, write_body
from src.infrastructure.archive import ArchiveWriter
from src.infrastructure.database.models import FileDB
from src.infrastructure.enums import ArchiveFormat
from src.infrastructure.integrations import TelegramClient, TelegramMessageGoneError
from src.infrastructure.security import DecryptionError


def entry_name(base: str, file: FileDB) -> str:
    inner = (
        file.folder_path.removeprefix(base).lstrip("/") if base else file.folder_path
    )
    return f"{inner}/{file.name}" if inner else file.name


async def build_archive(
    telegram: TelegramClient,
    chat_id: int,
    files: list[FileDB],
    base: str,
    archive_format: ArchiveFormat,
    target: Path,
    progress: Callable[[int], None],
    checkpoint: Callable[[], Awaitable[None]],
) -> list[str]:
    warnings: list[str] = []
    done = 0
    with target.open("wb") as output:
        target.chmod(0o600)
        writer = ArchiveWriter(archive_format, output)
        for file in files:
            await checkpoint()
            name = entry_name(base, file)
            part = temp_path(file.name)
            try:
                try:
                    media, size = await telegram.message_media(
                        chat_id, file.tg_message_id
                    )
                except TelegramMessageGoneError:
                    logger.warning(
                        "File {} is gone from Telegram, not archived", file.id
                    )
                    warnings.append(f"{name}: gone from Telegram, not archived")
                    continue
                if size != stored_size(file):
                    logger.warning("File {} changed in Telegram, not archived", file.id)
                    warnings.append(f"{name}: changed in Telegram, not archived")
                    continue
                offset = done
                try:
                    await write_body(
                        file_body(telegram, media, file, 0, file.size - 1),
                        part,
                        file.size,
                        lambda written: progress(offset + written),
                    )
                except DecryptionError as error:
                    logger.warning("File {} not archived: {}", file.id, error.message)
                    warnings.append(f"{name}: {error.message}, not archived")
                    continue
                await asyncio.to_thread(
                    writer.add, part, name, file.updated_at.timestamp()
                )
            finally:
                part.unlink(missing_ok=True)
            done += file.size
            progress(done)
        await asyncio.to_thread(writer.close)
    return warnings
