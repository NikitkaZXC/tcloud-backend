"""Нарезка, шифрование, загрузка и обратная сборка файлов."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from pathlib import Path
from typing import AsyncIterator

from . import crypto
from .db import Database, FileRecord, Part, now_iso
from .telegram import StorageBackend

log = logging.getLogger(__name__)

READ_CHUNK = 1024 * 1024


class TCloudService:
    def __init__(self, db: Database, backend: StorageBackend, master_key: bytes, part_size: int, segment_size: int, tmp_dir: Path):
        if part_size % segment_size:
            raise ValueError("part_size must be a multiple of segment_size")
        self.db = db
        self.backend = backend
        self.master_key = master_key
        self.part_size = part_size
        self.segment_size = segment_size
        self.tmp_dir = tmp_dir
        self._tasks: dict[str, asyncio.Task] = {}
        self._progress: dict[str, tuple[int, int]] = {}  # file_id -> (загружено байт, всего байт)

    async def start(self) -> None:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        for stale in await self.db.fail_interrupted():
            log.warning("upload %s was interrupted by restart, marked as failed", stale)
        for leftover in self.tmp_dir.iterdir():
            leftover.unlink(missing_ok=True)

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    def progress(self, file_id: str) -> tuple[int, int] | None:
        return self._progress.get(file_id)

    # ---------- загрузка ----------

    async def store(self, src: Path, name: str, mime: str | None) -> FileRecord:
        """Принимает файл с диска (он будет удалён) и запускает загрузку в фоне."""
        size = src.stat().st_size
        rec = FileRecord(
            id=uuid.uuid4().hex,
            name=name,
            mime=mime,
            size=size,
            sha256=None,
            salt=crypto.new_salt(),
            segment_size=self.segment_size,
            part_size=self.part_size,
            parts_total=max(1, -(-size // self.part_size)),
            status="uploading",
            error=None,
            created_at=now_iso(),
        )
        await self.db.create_file(rec)
        self._tasks[rec.id] = asyncio.create_task(self._upload(rec, src))
        return rec

    async def wait(self, file_id: str) -> None:
        task = self._tasks.get(file_id)
        if task is not None:
            await asyncio.shield(task)

    async def _upload(self, rec: FileRecord, src: Path) -> None:
        uploaded: list[Part] = []
        hasher = hashlib.sha256()
        total_enc = sum(
            crypto.encrypted_part_size(self._plain_len(rec, i), rec.segment_size) for i in range(rec.parts_total)
        )
        done = 0
        self._progress[rec.id] = (0, total_enc)
        try:
            for idx in range(rec.parts_total):
                part_path = self.tmp_dir / f"{rec.id}.{idx}.part"
                try:
                    enc_size = await asyncio.to_thread(self._encrypt_part, rec, src, idx, part_path, hasher)

                    async def on_progress(current: int, _total: int, base: int = done) -> None:
                        self._progress[rec.id] = (base + current, total_enc)

                    chat_id, message_id = await self.backend.upload(
                        part_path,
                        file_name=f"{rec.id}.{idx:04d}.tcloud",
                        caption=f"tcloud:{rec.id}:{idx + 1}/{rec.parts_total}",
                        progress=on_progress,
                    )
                finally:
                    part_path.unlink(missing_ok=True)
                part = Part(idx, chat_id, message_id, enc_size)
                uploaded.append(part)
                await self.db.add_part(rec.id, part)
                done += enc_size
                self._progress[rec.id] = (done, total_enc)
                log.info("file %s: part %d/%d uploaded", rec.id, idx + 1, rec.parts_total)
            await self.db.set_status(rec.id, "ready", sha256=hasher.hexdigest())
        except BaseException as exc:
            log.exception("file %s: upload failed", rec.id)
            await self.db.set_status(rec.id, "failed", error=f"{type(exc).__name__}: {exc}")
            await self._delete_parts(uploaded)
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            src.unlink(missing_ok=True)
            self._progress.pop(rec.id, None)
            self._tasks.pop(rec.id, None)

    def _plain_len(self, rec: FileRecord, idx: int) -> int:
        return max(0, min(rec.part_size, rec.size - idx * rec.part_size))

    def _encrypt_part(self, rec: FileRecord, src: Path, idx: int, dst: Path, hasher) -> int:
        """Шифрует одну часть в файл. Работает в отдельном потоке, чтобы не блокировать event loop."""
        enc = crypto.PartEncryptor(self.master_key, bytes.fromhex(rec.id), rec.salt, idx, rec.segment_size)
        remaining = self._plain_len(rec, idx)
        written = 0
        with open(src, "rb") as fin, open(dst, "wb") as fout:
            fin.seek(idx * rec.part_size)
            fout.write(enc.header)
            written += len(enc.header)
            while True:
                data = fin.read(min(rec.segment_size, remaining))
                if len(data) != min(rec.segment_size, remaining):
                    raise IOError("source file changed while uploading")
                remaining -= len(data)
                hasher.update(data)
                block = enc.encrypt_segment(data, last=remaining == 0)
                fout.write(block)
                written += len(block)
                if remaining == 0:
                    break
        return written

    # ---------- скачивание ----------

    async def iter_download(self, rec: FileRecord) -> AsyncIterator[bytes]:
        """Скачивает части по порядку, расшифровывает на лету и отдаёт исходные байты."""
        if len(rec.parts) != rec.parts_total:
            raise RuntimeError(f"file {rec.id}: expected {rec.parts_total} parts, have {len(rec.parts)}")
        hasher = hashlib.sha256()
        file_id = bytes.fromhex(rec.id)
        for part in rec.parts:
            dec = crypto.PartDecryptor(self.master_key, file_id, part.idx, rec.segment_size)
            async for chunk in self.backend.stream(part.chat_id, part.message_id):
                for plain in dec.feed(chunk):
                    hasher.update(plain)
                    yield plain
            for plain in dec.finish():
                hasher.update(plain)
                yield plain
        if rec.sha256 and hasher.hexdigest() != rec.sha256:
            raise crypto.CryptoError(f"file {rec.id}: sha256 mismatch after reassembly")

    # ---------- удаление ----------

    async def delete(self, rec: FileRecord) -> None:
        task = self._tasks.get(rec.id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            rec = await self.db.get_file(rec.id) or rec
        await self._delete_parts(rec.parts)
        await self.db.delete_file(rec.id)

    async def _delete_parts(self, parts: list[Part]) -> None:
        by_chat: dict[int, list[int]] = {}
        for p in parts:
            by_chat.setdefault(p.chat_id, []).append(p.message_id)
        for chat_id, ids in by_chat.items():
            try:
                await self.backend.delete(chat_id, ids)
            except Exception:
                log.exception("failed to delete messages %s in %s", ids, chat_id)
