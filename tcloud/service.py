from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import httpx
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telethon import errors as tg_errors

from . import limits, media, paths, security
from .config import Settings
from .db import ACTIVE, File, Folder, Storage, Task
from .errors import TCloudError, describe
from .gates import MiB, DiskBudget, Gate
from .telegram import TRANSIENT

log = logging.getLogger(__name__)
HOOK_INTERVAL = 2.0  # с между вебхуками с прогрессом
PREMIUM_TTL = 60.0  # с: Premium не меняется каждую секунду, а get_me — запрос в TG


def caption_token(file_id: uuid.UUID) -> str:
    # Одно «слово» без дефисов: поиск Telegram находит его целиком, а не по кускам
    return f"#tc{file_id.hex}"


def tg_link(chat_id: int, message_id: int) -> str:
    return f"https://t.me/c/{str(chat_id).removeprefix('-100')}/{message_id}"


def _esc(p: str) -> str:
    return p.replace("%", r"\%").replace("_", r"\_")  # '\' в путях запрещён, им и экранируем


def _under(col, folder: str):
    """Сама папка и всё, что внутри."""
    return or_(col == folder, col.like(_esc(folder + "/") + "%", escape="\\"))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _not_found(what: str) -> TCloudError:
    return TCloudError(404, f"{what}_not_found", f"{what} not found")


class Service:
    def __init__(self, settings: Settings, db: async_sessionmaker, tg, scanner):
        self.s, self.db, self.tg, self.scanner = settings, db, tg, scanner
        self.downloads = Gate("download", settings.download_concurrency)
        self.scans = Gate("scanning", settings.scan_concurrency)
        self.uploads = Gate("uploading_tg", settings.tg_concurrency)
        self.disk = DiskBudget(settings.tmp_dir, settings.min_free_disk_mb * MiB)
        self.tg_resume_at = 0.0  # monotonic: до этого момента выгрузка в TG на паузе (FloodWait)
        self._premium: tuple[float, bool] | None = None
        self.live: dict[uuid.UUID, dict] = {}  # прогресс идущих задач, в БД — только смена стадий
        self._bg: set[asyncio.Task] = set()

    # ---------- жизненный цикл ----------

    async def start(self) -> None:
        self.s.tmp_dir.mkdir(parents=True, exist_ok=True)
        for p in self.s.tmp_dir.iterdir():
            p.unlink(missing_ok=True)
        async with self.db.begin() as s:
            await s.execute(
                update(Task).where(Task.status.in_(("pending", "running")))
                .values(status="failed", error={"code": "interrupted", "message": "server restarted"}, updated_at=func.now())
            )
        await self._init_storages()

    async def stop(self) -> None:
        for t in list(self._bg):
            t.cancel()
        await asyncio.gather(*self._bg, return_exceptions=True)

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    def _tmp(self, name: str = "") -> Path:
        # Имя от пользователя в ФС не попадает, только его расширение (Telethon определяет по нему тип)
        suffix = f".{media.ext(name)}" if media.ext(name) else ""
        return self.s.tmp_dir / f"{uuid.uuid4().hex}{suffix}"

    async def _init_storages(self) -> None:
        for cid in self.s.chat_ids:
            async with self.db.begin() as s:
                if await s.scalar(select(Storage.id).where(Storage.tg_chat_id == cid)):
                    continue
                try:
                    await self.tg.check_channel(cid)
                except Exception as e:
                    log.error("storage %s from TCLOUD_CHATS is not connected: %s", cid, e)
                    continue
                s.add(Storage(tg_chat_id=cid))
                log.info("storage %s connected from TCLOUD_CHATS", cid)

    async def health(self) -> dict:
        try:
            async with self.db() as s:
                await s.execute(select(1))
            db_ok = True
        except Exception:
            db_ok = False
        out = {"db": db_ok, "telegram": self.tg.connected(), "antivirus": await self.scanner.ping()}
        return {"ok": all(out.values()), **out}

    async def premium(self) -> bool:
        if not self._premium or time.monotonic() - self._premium[0] > PREMIUM_TTL:
            self._premium = (time.monotonic(), await self.tg.is_premium())
        return self._premium[1]

    async def limit(self) -> int:
        return limits.file_limit(await self.premium())

    async def account(self) -> dict:
        self._premium = None  # ручка /account всегда спрашивает Telegram заново
        premium = await self.premium()
        return {"premium": premium, "file_limit": limits.file_limit(premium)}

    # ---------- хранилища ----------

    async def _storage(self, s: AsyncSession, sid: uuid.UUID) -> Storage:
        st = await s.get(Storage, sid)
        if not st:
            raise _not_found("storage")
        return st

    def _storage_json(self, st: Storage, info) -> dict:
        return {
            "id": st.id,
            "path": paths.join(st.id, ""),
            "name": info.title if info else None,
            "description": info.about if info else None,
            "has_photo": bool(info and info.has_photo),
            "available": info is not None,
            "telegram": {"chat_id": st.tg_chat_id},
            "created_at": st.created_at,
            "updated_at": st.updated_at,
        }

    async def create_storage(self, name: str, description: str | None) -> dict:
        cid = await self.tg.create_channel(name, description or "")
        async with self.db.begin() as s:
            st = Storage(tg_chat_id=cid)
            s.add(st)
        return await self.get_storage(st.id)

    async def list_storages(self) -> list[dict]:
        async with self.db() as s:
            rows = (await s.scalars(select(Storage).order_by(Storage.created_at))).all()
        infos = await self.tg.channels([r.tg_chat_id for r in rows])
        return [self._storage_json(r, infos.get(r.tg_chat_id)) for r in rows]

    async def get_storage(self, sid: uuid.UUID) -> dict:
        async with self.db() as s:
            st = await self._storage(s, sid)
            count, total = (await s.execute(
                select(func.count(), func.coalesce(func.sum(File.size), 0)).where(File.storage_id == sid)
            )).one()
        info = (await self.tg.channels([st.tg_chat_id])).get(st.tg_chat_id)
        return self._storage_json(st, info) | {"files": count, "total_size": total}

    async def _touch_storage(self, sid: uuid.UUID) -> None:
        async with self.db.begin() as s:
            await s.execute(update(Storage).where(Storage.id == sid).values(updated_at=func.now()))

    async def update_storage(self, sid: uuid.UUID, name: str | None, description: str | None) -> dict:
        async with self.db() as s:
            st = await self._storage(s, sid)
        if name is not None:
            await self.tg.edit_title(st.tg_chat_id, name)
        if description is not None:
            await self.tg.edit_about(st.tg_chat_id, description)
        await self._touch_storage(sid)
        return await self.get_storage(sid)

    async def set_storage_photo(self, sid: uuid.UUID, body: AsyncIterator[bytes]) -> dict:
        async with self.db() as s:
            st = await self._storage(s, sid)
        tmp = self._tmp()
        try:
            await self._receive(body, tmp, limits.PHOTO_MAX)
            m = await asyncio.to_thread(media.probe, tmp, None)
            if m.kind != "photo":
                raise TCloudError(415, "unsupported_media_type", "storage photo must be a still image")
            await self.scanner.scan(tmp)
            if m.mime not in ("image/jpeg", "image/png"):
                jpg = self._tmp("photo.jpg")
                await asyncio.to_thread(media.to_jpeg, tmp, jpg)
                tmp.unlink()
                tmp = jpg
            await self.tg.set_photo(st.tg_chat_id, tmp)
        finally:
            tmp.unlink(missing_ok=True)
        await self._touch_storage(sid)
        return await self.get_storage(sid)

    async def delete_storage_photo(self, sid: uuid.UUID) -> dict:
        async with self.db() as s:
            st = await self._storage(s, sid)
        await self.tg.delete_photo(st.tg_chat_id)
        await self._touch_storage(sid)
        return await self.get_storage(sid)

    async def storage_photo(self, sid: uuid.UUID) -> bytes:
        async with self.db() as s:
            st = await self._storage(s, sid)
        data = await self.tg.photo(st.tg_chat_id)
        if not data:
            raise _not_found("photo")
        return data

    # ---------- папки ----------

    async def _ensure_folders(self, s: AsyncSession, sid: uuid.UUID, folder: str) -> None:
        """Создаёт папку и все родительские; существующие не трогает (папки объединяются)."""
        if not folder:
            return
        segs = folder.split("/")
        rows = [{"id": uuid.uuid4(), "storage_id": sid, "path": "/".join(segs[:i])} for i in range(1, len(segs) + 1)]
        await s.execute(pg_insert(Folder).values(rows).on_conflict_do_nothing())

    def _folder_json(self, f: Folder) -> dict:
        return {
            "id": f.id, "path": paths.join(f.storage_id, f.path), "name": f.path.rpartition("/")[2],
            "created_at": f.created_at, "updated_at": f.updated_at,
        }

    async def _folder_exists(self, s: AsyncSession, sid: uuid.UUID, folder: str) -> bool:
        return not folder or bool(await s.scalar(select(Folder.id).where(Folder.storage_id == sid, Folder.path == folder)))

    async def create_folder(self, raw: str) -> dict:
        sid, folder = paths.split_folder(raw)
        if not folder:
            raise TCloudError(422, "invalid_path", "storage root always exists")
        async with self.db.begin() as s:
            await self._storage(s, sid)
            await self._ensure_folders(s, sid, folder)
            f = await s.scalar(select(Folder).where(Folder.storage_id == sid, Folder.path == folder))
        return self._folder_json(f)

    async def list_folder(self, raw: str) -> dict:
        sid, folder = paths.split_folder(raw)
        prefix = _esc(folder + "/" if folder else "")
        async with self.db() as s:
            st = await self._storage(s, sid)
            if not await self._folder_exists(s, sid, folder):
                raise _not_found("folder")
            sub = (await s.scalars(
                select(Folder).where(
                    Folder.storage_id == sid,
                    Folder.path.like(prefix + "%", escape="\\"),
                    Folder.path.not_like(prefix + "%/%", escape="\\"),
                ).order_by(Folder.path)
            )).all()
            files = (await s.scalars(
                select(File).where(File.storage_id == sid, File.folder_path == folder).order_by(File.name)
            )).all()
        return {
            "path": paths.join(sid, folder),
            "folders": [self._folder_json(f) for f in sub],
            "files": [self._file_json(f, st.tg_chat_id) for f in files],
        }

    async def delete_folder(self, raw: str) -> dict:
        sid, folder = paths.split_folder(raw)
        if not folder:
            raise TCloudError(422, "invalid_path", "storage root cannot be deleted")
        async with self.db() as s:
            st = await self._storage(s, sid)
            if not await self._folder_exists(s, sid, folder):
                raise _not_found("folder")
            files = (await s.execute(
                select(File.id, File.tg_message_id).where(File.storage_id == sid, _under(File.folder_path, folder))
            )).all()
        # Сначала Telegram: если он упадёт, записи в БД останутся и удаление можно повторить
        await self.tg.delete(st.tg_chat_id, [f.tg_message_id for f in files])
        async with self.db.begin() as s:
            if files:
                await s.execute(delete(File).where(File.id.in_([f.id for f in files])))
            await s.execute(delete(Folder).where(Folder.storage_id == sid, _under(Folder.path, folder)))
        return {"path": paths.join(sid, folder), "deleted_files": len(files)}

    # ---------- файлы ----------

    def _file_json(self, f: File, chat_id: int) -> dict:
        return {
            "uuid": f.id,
            "path": paths.join(f.storage_id, f.folder_path, f.name),
            "storage_id": f.storage_id,
            "name": f.name,
            "size": f.size,
            "mime": f.mime,
            "kind": f.kind,
            "sent_as": f.sent_as,
            "width": f.width,
            "height": f.height,
            "duration": f.duration,
            "sha256": f.sha256,
            "created_at": f.created_at,
            "updated_at": f.updated_at,
            "telegram": {
                "chat_id": chat_id,
                "message_id": f.tg_message_id,
                "link": tg_link(chat_id, f.tg_message_id),
                "search": caption_token(f.id),
            },
        }

    async def _file(self, s: AsyncSession, fid: uuid.UUID) -> tuple[File, int]:
        row = (await s.execute(select(File, Storage.tg_chat_id).join(Storage).where(File.id == fid))).first()
        if not row:
            raise _not_found("file")
        return row[0], row[1]

    async def get_file(self, fid: uuid.UUID) -> dict:
        async with self.db() as s:
            return self._file_json(*await self._file(s, fid))

    async def get_file_by_path(self, raw: str) -> dict:
        sid, folder, name = paths.split_file(raw)
        async with self.db() as s:
            st = await self._storage(s, sid)
            f = await s.scalar(select(File).where(File.storage_id == sid, File.folder_path == folder, File.name == name))
        if not f:
            raise _not_found("file")
        return self._file_json(f, st.tg_chat_id)

    async def delete_file(self, fid: uuid.UUID) -> None:
        async with self.db() as s:
            f, cid = await self._file(s, fid)
        await self.tg.delete(cid, [f.tg_message_id])
        async with self.db.begin() as s:
            await s.execute(delete(File).where(File.id == fid))

    async def open_download(self, fid: uuid.UUID) -> tuple[File, object]:
        async with self.db() as s:
            f, cid = await self._file(s, fid)
        return f, await self.tg.message_media(cid, f.tg_message_id)

    # ---------- загрузки ----------

    async def _existing(self, s: AsyncSession, sid: uuid.UUID, folder: str, name: str, on_conflict: str) -> File | None:
        await self._storage(s, sid)
        f = await s.scalar(select(File).where(File.storage_id == sid, File.folder_path == folder, File.name == name))
        if f and on_conflict == "error":
            raise TCloudError(409, "conflict", f"{paths.join(sid, folder, name)} already exists")
        return f

    async def _taken(self, s: AsyncSession, sid: uuid.UUID, folder: str, name: str) -> bool:
        """Имя занято файлом или идущей загрузкой."""
        if await s.scalar(select(File.id).where(File.storage_id == sid, File.folder_path == folder, File.name == name)):
            return True
        return bool(await s.scalar(select(Task.id).where(Task.path == paths.join(sid, folder, name), Task.status.in_(ACTIVE))))

    async def _free_name(self, s: AsyncSession, sid: uuid.UUID, folder: str, name: str) -> str:
        stem, dot, ext = name.rpartition(".")
        for i in range(1, 10_000):
            candidate = f"{stem} ({i}){dot}{ext}"
            if not await self._taken(s, sid, folder, candidate):
                paths.check_segment(candidate)
                return candidate
        raise TCloudError(409, "conflict", "no free name left")

    async def new_task(
        self, path: str, send_as: str, on_conflict: str, callback_url: str | None, size: int | None,
        batch_id: uuid.UUID | None = None, **params,
    ) -> Task:
        sid, folder, name = paths.split_file(path)
        media.check_name(name)
        if callback_url:
            security.check_scheme(callback_url)
        limit = await self.limit()
        if size is not None and size > limit:
            raise TCloudError(413, "too_large", f"file is {size} bytes, account limit is {limit}", limit=limit)
        if size is not None:
            self.disk.check(size)
        async with self.db.begin() as s:
            # Задачи, которым так и не прислали файл, не должны вечно держать путь и место в лимите
            await s.execute(
                update(Task).where(Task.status == "pending", Task.created_at < func.now() - timedelta(seconds=self.s.pending_ttl))
                .values(status="failed", error={"code": "expired", "message": "file was not sent in time"}, updated_at=func.now())
            )
            active = await s.scalar(select(func.count()).select_from(Task).where(Task.status.in_(ACTIVE)))
            if active >= self.s.max_active_tasks:
                raise TCloudError(429, "too_many_tasks", f"{active} uploads are already in progress, try later", retry_after=30)
            existing = await self._existing(s, sid, folder, name, on_conflict)
            if on_conflict == "keep_both" and await self._taken(s, sid, folder, name):
                name, existing = await self._free_name(s, sid, folder, name), None
            t = Task(
                status="pending", stage="receiving", bytes_total=size, callback_url=callback_url, batch_id=batch_id,
                path=paths.join(sid, folder, name),
                params={"path": paths.join(sid, folder, name), "send_as": send_as, "on_conflict": on_conflict, **params},
            )
            if existing and on_conflict == "skip":
                t.status, t.stage, t.file_id, t.warnings = "done", "done", existing.id, ["skipped: file already exists"]
            s.add(t)
            try:
                await s.flush()
            except IntegrityError:
                raise TCloudError(409, "upload_in_progress", f"{t.path} is already being uploaded") from None
        return t

    async def create_upload(self, path: str, size: int, send_as: str, on_conflict: str, callback_url: str | None, batch_id=None) -> dict:
        t = await self.new_task(path, send_as, on_conflict, callback_url, size, batch_id, source="device")
        return await self.get_task(t.id)

    async def create_url_upload(self, url: str, path: str, send_as: str, on_conflict: str, callback_url: str | None, batch_id=None) -> dict:
        security.check_scheme(url)
        t = await self.new_task(path, send_as, on_conflict, callback_url, None, batch_id, source="url", url=url)
        if t.status == "pending":
            await self._claim(t)
            tmp = self._tmp(t.path)

            async def receive() -> None:
                async with self._gate(t, self.downloads):
                    res = self.disk.reserve(0)
                    try:
                        def progress(done: int, total: int | None) -> None:
                            res.update(done, total)
                            self._progress(t.id, done, total)

                        await security.download(url, tmp, await self.limit(), progress)
                    finally:
                        res.release()

            self._spawn(self._pipeline(t, tmp, receive))
        return await self.get_task(t.id)

    async def create_batch(self, items: list[dict]) -> dict:
        """До 100 загрузок одним запросом; ошибка одной не мешает остальным."""
        batch_id = uuid.uuid4()
        results = []
        for item in items:
            try:
                if item.get("url"):
                    task = await self.create_url_upload(item["url"], item["path"], item["send_as"], item["on_conflict"], item.get("callback_url"), batch_id)
                else:
                    task = await self.create_upload(item["path"], item["size"], item["send_as"], item["on_conflict"], item.get("callback_url"), batch_id)
                results.append({"path": item["path"], "task": task, "error": None})
            except TCloudError as e:
                results.append({"path": item["path"], "task": None, "error": describe(e)[1]})
        return {"batch_id": batch_id, "items": results}

    async def _claim(self, t: Task) -> None:
        """pending → running атомарно: второй PUT на ту же задачу получит 409."""
        async with self.db.begin() as s:
            ok = await s.scalar(
                update(Task).where(Task.id == t.id, Task.status == "pending")
                .values(status="running", updated_at=func.now()).returning(Task.id)
            )
        if not ok:
            raise TCloudError(409, "task_not_pending", "this task is not waiting for a file")
        self.live[t.id] = {
            "stage": "receiving", "bytes_done": 0, "bytes_total": t.bytes_total, "started": time.monotonic(),
            "hook_at": 0.0, "callback_url": t.callback_url, "gate": None, "waiting": None,
        }
        self._hook(t.id)

    async def receive_upload(self, tid: uuid.UUID, body: AsyncIterator[bytes]) -> dict:
        async with self.db() as s:
            t = await s.get(Task, tid)
        if not t:
            raise _not_found("task")
        if t.params.get("source") != "device":
            raise TCloudError(409, "task_not_pending", "this task is not waiting for a file")
        res = self.disk.reserve(t.bytes_total)
        try:
            await self._claim(t)
        except BaseException:
            res.release()
            raise
        tmp = self._tmp(t.path)
        try:
            def progress(n: int) -> None:
                res.update(n)
                self._progress(tid, n)

            got = await self._receive(body, tmp, min(t.bytes_total, await self.limit()), progress)
            if got != t.bytes_total:
                raise TCloudError(400, "size_mismatch", f"declared {t.bytes_total} bytes, received {got}")
        except BaseException as e:
            tmp.unlink(missing_ok=True)
            await self._fail(t, e)
            raise
        finally:
            res.release()  # файл целиком на диске — дальше его видит сам disk_usage
        self._spawn(self._pipeline(t, tmp))
        return await self.get_task(tid)

    async def _receive(self, body: AsyncIterator[bytes], dest: Path, limit: int, progress: Callable[[int], None] | None = None) -> int:
        done = 0
        with open(dest, "wb") as f:
            dest.chmod(0o600)
            async for chunk in body:
                done += len(chunk)
                if done > limit:
                    raise TCloudError(413, "too_large", f"file is larger than {limit} bytes", limit=limit)
                f.write(chunk)
                if progress:
                    progress(done)
        return done

    async def _pipeline(self, t: Task, tmp: Path, receive: Callable | None = None) -> None:
        extra: list[Path] = []
        try:
            if receive:
                await receive()
            await self._process(t, tmp, extra)
        except BaseException as e:
            await self._fail(t, e)
            if isinstance(e, asyncio.CancelledError):
                raise
        finally:
            for p in (tmp, *extra):
                p.unlink(missing_ok=True)

    @asynccontextmanager
    async def _gate(self, t: Task, gate: Gate):
        live = self.live[t.id]
        live["gate"] = gate
        self._hook(t.id)
        try:
            async with gate.slot(t.id):
                live["gate"] = None
                live["started"] = time.monotonic()
                yield
        finally:
            live["gate"] = None

    async def _retry(self, t: Task, call: Callable, transient: Callable[[BaseException], bool]):
        """Повтор с растущей паузой при временных сбоях (сеть, ClamAV перезапускается)."""
        for attempt in range(self.s.retries + 1):
            try:
                return await call()
            except BaseException as e:
                if attempt == self.s.retries or not transient(e):
                    raise
                delay = self.s.retry_delay * 2 ** attempt
                log.warning("task %s: %r, retry %d in %.1f s", t.id, e, attempt + 1, delay)
                await self._wait(t, "retry", delay)

    async def _wait(self, t: Task, reason: str, seconds: float) -> None:
        live = self.live.get(t.id)
        if live:
            live["waiting"] = {"reason": reason, "until": time.time() + seconds}
            self._hook(t.id)
        try:
            await asyncio.sleep(seconds)
        finally:
            if live:
                live["waiting"] = None

    async def _send_tg(self, t: Task, **kw):
        """Выгрузка в TG: FloodWait ставит на паузу всю выгрузку, а не роняет задачи по одной."""
        while True:
            pause = self.tg_resume_at - time.monotonic()
            if pause > 0:
                await self._wait(t, "flood_wait", pause)
                continue
            try:
                return await self._retry(t, lambda: self.tg.send(**kw), lambda e: isinstance(e, TRANSIENT))
            except tg_errors.FloodWaitError as e:
                if e.seconds > self.s.tg_flood_max_wait:
                    raise
                self.tg_resume_at = max(self.tg_resume_at, time.monotonic() + e.seconds)
                log.warning("telegram flood wait %s s: all uploads paused", e.seconds)

    async def _process(self, t: Task, tmp: Path, extra: list[Path]) -> None:
        p = t.params
        sid, folder, name = paths.split_file(t.path)

        await self._stage(t, "scanning", None)
        async with self._gate(t, self.scans):
            m = await asyncio.to_thread(media.probe, tmp, name)
            sha = await asyncio.to_thread(_sha256, tmp)
            await self._retry(t, lambda: self.scanner.scan(tmp), lambda e: getattr(e, "code", None) == "scanner_unavailable")

            as_file = p["send_as"] == "file"
            warnings: list[str] = []
            send_path, thumb = tmp, None
            if not as_file and m.kind == "photo":
                if not media.photo_fits(m, tmp.stat().st_size):
                    as_file = True
                    warnings.append("photo does not fit Telegram photo limits (10 MB, sides), sent as file")
                elif m.mime not in ("image/jpeg", "image/png"):
                    send_path = self._tmp("photo.jpg")
                    extra.append(send_path)
                    await asyncio.to_thread(media.to_jpeg, tmp, send_path)
            if not as_file and m.kind == "video" and m.mime != "video/mp4":
                warnings.append(f"{m.mime} may be shown as a file instead of a player in Telegram")
            if m.kind == "video":
                thumb = self._tmp("thumb.jpg")
                extra.append(thumb)
                if not await asyncio.to_thread(media.thumbnail, tmp, thumb, m.duration):
                    thumb = None

        # Конфликт проверяем ещё раз: файл мог появиться, пока этот грузился
        async with self.db() as s:
            existing = await self._existing(s, sid, folder, name, p["on_conflict"])
            st = await s.get(Storage, sid)
            if existing and p["on_conflict"] == "skip":
                await self._finish(t, existing.id, ["skipped: file already exists"])
                return
            if existing and p["on_conflict"] == "keep_both":
                raise TCloudError(409, "conflict", f"{t.path} was taken during upload")
        fid = existing.id if existing else uuid.uuid4()  # replace сохраняет UUID

        await self._stage(t, "uploading_tg", send_path.stat().st_size)
        async with self._gate(t, self.uploads):
            sent = await self._send_tg(
                t, chat_id=st.tg_chat_id, path=send_path, name=name, caption=caption_token(fid), media=m,
                as_file=as_file, thumb=thumb, progress=lambda done, total: self._progress(t.id, done, total),
            )
        compressed = m.kind == "photo" and not as_file
        values = dict(
            folder_path=folder, name=name, size=sent.size, mime=(sent.mime or "image/jpeg") if compressed else m.mime,
            kind=m.kind, sent_as="file" if as_file else "media", width=sent.width or m.width,
            height=sent.height or m.height, duration=m.duration, sha256=sha, tg_message_id=sent.message_id,
        )
        try:
            async with self.db.begin() as s:
                await self._ensure_folders(s, sid, folder)
                if existing:
                    await s.execute(update(File).where(File.id == fid).values(**values, updated_at=func.now()))
                else:
                    s.add(File(id=fid, storage_id=sid, **values))
        except BaseException:
            await self.tg.delete(st.tg_chat_id, [sent.message_id])  # без записи в БД сообщение — мусор
            raise
        if existing:
            await self.tg.delete(st.tg_chat_id, [existing.tg_message_id])
        await self._finish(t, fid, warnings)

    # ---------- задачи и прогресс ----------

    async def _save(self, tid: uuid.UUID, **values) -> None:
        async with self.db.begin() as s:
            await s.execute(update(Task).where(Task.id == tid).values(**values, updated_at=func.now()))

    async def _stage(self, t: Task, stage: str, total: int | None) -> None:
        live = self.live[t.id]
        live.update(stage=stage, bytes_done=0, bytes_total=total, started=time.monotonic())
        await self._save(t.id, stage=stage, bytes_done=0, bytes_total=total)
        self._hook(t.id)

    def _progress(self, tid: uuid.UUID, done: int, total: int | None = None) -> None:
        live = self.live.get(tid)
        if not live:
            return
        live["bytes_done"] = done
        if total:
            live["bytes_total"] = total
        if time.monotonic() - live["hook_at"] >= HOOK_INTERVAL:
            self._hook(tid)

    async def _finish(self, t: Task, fid: uuid.UUID, warnings: list[str]) -> None:
        live = self.live.pop(t.id, {})
        await self._save(t.id, status="done", stage="done", file_id=fid, warnings=warnings,
                         bytes_done=live.get("bytes_total") or live.get("bytes_done") or 0)
        self._hook(t.id, t.callback_url)

    async def _fail(self, t: Task, e: BaseException) -> None:
        status, err = describe(e)
        if status >= 500 and not isinstance(e, asyncio.CancelledError):
            log.exception("task %s failed", t.id, exc_info=e)
        live = self.live.pop(t.id, {})
        try:
            await asyncio.shield(self._save(t.id, status="failed", error=err, bytes_done=live.get("bytes_done", 0)))
        except Exception:
            log.exception("cannot save failed task %s", t.id)
        self._hook(t.id, t.callback_url)

    def _task_json(self, t: Task, file: dict | None) -> dict:
        out = {
            "id": t.id, "batch_id": t.batch_id, "status": t.status, "stage": t.stage, "path": t.path,
            "bytes_done": t.bytes_done, "bytes_total": t.bytes_total, "progress": None, "speed": None, "eta": None,
            "queue_position": None, "waiting": None,
            "error": t.error, "warnings": t.warnings, "file": file, "created_at": t.created_at, "updated_at": t.updated_at,
        }
        live = self.live.get(t.id)
        if live and t.status == "running":
            out.update(stage=live["stage"], bytes_done=live["bytes_done"], bytes_total=live["bytes_total"])
            if live["gate"]:
                out["queue_position"] = live["gate"].position(t.id)
                out["waiting"] = {"reason": f"queue:{live['gate'].name}"}
            elif live["waiting"]:
                out["waiting"] = {"reason": live["waiting"]["reason"], "seconds": max(0, round(live["waiting"]["until"] - time.time()))}
            elapsed = time.monotonic() - live["started"]
            if elapsed > 0 and live["bytes_done"] and not out["waiting"]:
                out["speed"] = round(live["bytes_done"] / elapsed)
                if out["bytes_total"]:
                    out["eta"] = round((out["bytes_total"] - live["bytes_done"]) / out["speed"], 1)
        if t.status == "done":
            out["progress"] = 100.0
        elif out["bytes_total"]:
            out["progress"] = round(100 * out["bytes_done"] / out["bytes_total"], 1)
        return out

    async def _tasks_json(self, s: AsyncSession, tasks: list[Task]) -> list[dict]:
        ids = [t.file_id for t in tasks if t.file_id]
        files = {}
        if ids:  # одним запросом, а не по запросу на задачу
            for f, cid in (await s.execute(select(File, Storage.tg_chat_id).join(Storage).where(File.id.in_(ids)))).all():
                files[f.id] = self._file_json(f, cid)
        return [self._task_json(t, files.get(t.file_id)) for t in tasks]

    async def get_task(self, tid: uuid.UUID) -> dict:
        async with self.db() as s:
            t = await s.get(Task, tid)
            if not t:
                raise _not_found("task")
            return (await self._tasks_json(s, [t]))[0]

    async def list_tasks(self, batch_id: uuid.UUID | None, status: str | None, limit: int, offset: int) -> dict:
        where = []
        if batch_id:
            where.append(Task.batch_id == batch_id)
        if status:
            where.append(Task.status == status)
        async with self.db() as s:
            rows = (await s.scalars(select(Task).where(*where).order_by(Task.created_at.desc(), Task.id).limit(limit).offset(offset))).all()
            items = await self._tasks_json(s, list(rows))
            counts = dict((await s.execute(select(Task.status, func.count()).where(*where).group_by(Task.status))).all())
        out = {"items": items, "summary": {"total": sum(counts.values()), "by_status": counts}}
        if batch_id:  # общий прогресс пачки — по всем её задачам, не только по странице
            async with self.db() as s:
                all_tasks = (await s.scalars(select(Task).where(Task.batch_id == batch_id))).all()
            views = [self._task_json(t, None) for t in all_tasks]
            done = sum(v["bytes_done"] or 0 for v in views)
            total = sum(v["bytes_total"] or 0 for v in views)
            finished = sum(v["status"] in ("done", "failed") for v in views)
            out["summary"] |= {"bytes_done": done, "bytes_total": total, "finished": finished,
                               "progress": round(100 * finished / len(views), 1) if views else None}
        return out

    def _hook(self, tid: uuid.UUID, url: str | None = None) -> None:
        live = self.live.get(tid)
        url = url or (live and live["callback_url"])
        if not url:
            return
        if live:
            live["hook_at"] = time.monotonic()
        self._spawn(self._send_hook(tid, url))

    async def _send_hook(self, tid: uuid.UUID, url: str) -> None:
        body = json.dumps(jsonable_encoder(await self.get_task(tid))).encode()
        sig = hmac.new(self.s.api_key.encode(), body, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "X-TCloud-Signature": f"sha256={sig}"}
        for delay in (1, 2, 4, None):
            try:
                async with security.open_url(url, "POST", headers=headers, content=body) as r:
                    if r.status_code < 500:
                        return
            except TCloudError as e:
                log.warning("webhook %s rejected: %s", url, e.message)
                return
            except httpx.HTTPError as e:
                log.warning("webhook %s failed: %s", url, e)
            if delay:
                await asyncio.sleep(delay)
