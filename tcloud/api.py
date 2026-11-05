from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
import uuid
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Security, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader

from . import crypto
from .config import Settings
from .db import Database, FileRecord
from .service import TCloudService
from .telegram import TelegramBackend

log = logging.getLogger(__name__)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def build_service(settings: Settings) -> TCloudService:
    backend = TelegramBackend(
        api_id=settings.tg_api_id,
        api_hash=settings.tg_api_hash,
        chat_id=settings.chat_id,
        workdir=settings.data_dir,
        bot_token=settings.tg_bot_token,
        session_string=settings.tg_session_string,
        proxy_url=settings.tg_proxy_url,
    )
    return TCloudService(
        db=Database(settings.db_path),
        backend=backend,
        master_key=crypto.load_master_key(settings.master_key),
        part_size=settings.part_size,
        segment_size=settings.segment_size,
        tmp_dir=settings.tmp_dir,
    )


def create_app(settings: Settings | None = None, service: TCloudService | None = None) -> FastAPI:
    settings = settings or Settings()
    service = service or build_service(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await service.db.open()
        await service.backend.start()
        await service.start()
        try:
            yield
        finally:
            await service.stop()
            await service.backend.stop()
            await service.db.close()

    def require_key(key: str | None = Security(api_key_header)) -> None:
        if not key or not secrets.compare_digest(key, settings.api_key):
            raise HTTPException(401, "invalid or missing X-API-Key")

    app = FastAPI(title="TCloud", version="0.1.0", lifespan=lifespan)

    def to_json(rec: FileRecord) -> dict:
        out = {
            "id": rec.id,
            "name": rec.name,
            "mime": rec.mime,
            "size": rec.size,
            "sha256": rec.sha256,
            "parts": rec.parts_total,
            "status": rec.status,
            "error": rec.error,
            "created_at": rec.created_at,
        }
        prog = service.progress(rec.id)
        if prog:
            out["progress"] = round(prog[0] / prog[1], 4) if prog[1] else 0.0
        return out

    async def get_or_404(file_id: str) -> FileRecord:
        rec = await service.db.get_file(file_id)
        if rec is None:
            raise HTTPException(404, "file not found")
        return rec

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.post("/files", status_code=202, dependencies=[Depends(require_key)])
    async def upload(file: UploadFile = File(...), wait: bool = Query(False, description="ждать окончания загрузки")):
        tmp = settings.tmp_dir / f"upload-{uuid.uuid4().hex}"
        try:
            with open(tmp, "wb") as out:
                await asyncio.to_thread(shutil.copyfileobj, file.file, out, 4 * 1024 * 1024)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        rec = await service.store(tmp, file.filename or "file", file.content_type)
        if wait:
            await service.wait(rec.id)
            rec = await get_or_404(rec.id)
        return to_json(rec)

    @app.get("/files", dependencies=[Depends(require_key)])
    async def list_files(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return [to_json(r) for r in await service.db.list_files(limit, offset)]

    @app.get("/files/{file_id}", dependencies=[Depends(require_key)])
    async def file_info(file_id: str):
        return to_json(await get_or_404(file_id))

    @app.get("/files/{file_id}/download", dependencies=[Depends(require_key)])
    async def download(file_id: str):
        rec = await get_or_404(file_id)
        if rec.status != "ready":
            raise HTTPException(409, f"file is {rec.status}")
        return StreamingResponse(
            service.iter_download(rec),
            media_type=rec.mime or "application/octet-stream",
            headers={
                "Content-Length": str(rec.size),
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(rec.name)}",
            },
        )

    @app.delete("/files/{file_id}", status_code=204, dependencies=[Depends(require_key)])
    async def delete(file_id: str):
        await service.delete(await get_or_404(file_id))

    return app
