from __future__ import annotations

import re
import secrets
import uuid
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, Request, Response, Security
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException
from telethon import errors as tg_errors

from . import __version__, db, limits
from .config import Settings
from .errors import TCloudError, describe
from .security import ClamAV
from .service import Service
from .telegram import Telegram

SendAs = Literal["media", "file"]
OnConflict = Literal["error", "replace", "keep_both", "skip"]


class StorageIn(BaseModel):
    name: str = Field(min_length=1, max_length=limits.TITLE_MAX)
    description: str | None = Field(None, max_length=limits.ABOUT_MAX)


class StoragePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=limits.TITLE_MAX)
    description: str | None = Field(None, max_length=limits.ABOUT_MAX)


class FolderIn(BaseModel):
    path: str


class UploadIn(BaseModel):
    path: str = Field(description="tcloud/{storage}/{folders}/{name}")
    size: int = Field(gt=0, description="размер файла в байтах")
    send_as: SendAs = "file"
    on_conflict: OnConflict = "error"
    callback_url: str | None = None


class UrlUploadIn(BaseModel):
    url: str
    path: str = Field(description="tcloud/{storage}/{folders}/{name}; имя может отличаться от имени в ссылке")
    send_as: SendAs = "file"
    on_conflict: OnConflict = "error"
    callback_url: str | None = None


def parse_range(header: str, size: int) -> tuple[int, int]:
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not m or m.groups() == ("", ""):
        raise TCloudError(416, "bad_range", "only a single bytes=start-end range is supported")
    a, b = m.groups()
    if a:
        start, end = int(a), min(int(b), size - 1) if b else size - 1
    else:
        start, end = max(size - int(b), 0), size - 1
    if start > end or start >= size:
        raise TCloudError(416, "bad_range", f"range is outside of the file ({size} bytes)")
    return start, end


def _error(status: int, body: dict) -> JSONResponse:
    return JSONResponse({"error": jsonable_encoder(body)}, status)


def create_app(settings: Settings | None = None, tg=None, scanner=None) -> FastAPI:
    settings = settings or Settings()
    header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def require_key(key: str | None = Security(header)) -> None:
        if not key or not secrets.compare_digest(key.encode(), settings.api_key.encode()):
            raise TCloudError(401, "unauthorized", "invalid or missing X-API-Key")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine, sessions = db.connect(settings.database_url)
        client = tg or Telegram(settings.tg_session, settings.tg_api_id, settings.tg_api_hash)
        await client.start()
        svc = Service(settings, sessions, client, scanner or ClamAV(settings.clamd_host, settings.clamd_port))
        await svc.start()
        app.state.svc = svc
        try:
            yield
        finally:
            await svc.stop()
            await client.stop()
            await engine.dispose()

    app = FastAPI(title="TCloud", version=__version__, lifespan=lifespan, dependencies=[Depends(require_key)])

    def service(request: Request) -> Service:
        return request.app.state.svc

    Svc = Depends(service)

    @app.exception_handler(TCloudError)
    @app.exception_handler(tg_errors.RPCError)
    async def _app_error(_: Request, e: Exception):
        return _error(*describe(e))

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, e: HTTPException):
        return _error(e.status_code, {"code": "http_error", "message": str(e.detail)})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, e: RequestValidationError):
        return _error(422, {"code": "validation_error", "message": "invalid request", "details": e.errors()})

    @app.get("/health", tags=["system"])
    async def health(svc: Service = Svc):
        return await svc.health()

    @app.get("/account", tags=["system"])
    async def account(svc: Service = Svc):
        return await svc.account()

    # ---------- хранилища ----------

    @app.post("/storages", status_code=201, tags=["storages"])
    async def create_storage(body: StorageIn, svc: Service = Svc):
        return await svc.create_storage(body.name, body.description)

    @app.get("/storages", tags=["storages"])
    async def list_storages(svc: Service = Svc):
        return await svc.list_storages()

    @app.get("/storages/{sid}", tags=["storages"])
    async def get_storage(sid: uuid.UUID, svc: Service = Svc):
        return await svc.get_storage(sid)

    @app.patch("/storages/{sid}", tags=["storages"])
    async def update_storage(sid: uuid.UUID, body: StoragePatch, svc: Service = Svc):
        return await svc.update_storage(sid, body.name, body.description)

    @app.put("/storages/{sid}/photo", tags=["storages"], description="тело запроса — изображение")
    async def set_photo(sid: uuid.UUID, request: Request, svc: Service = Svc):
        return await svc.set_storage_photo(sid, request.stream())

    @app.delete("/storages/{sid}/photo", tags=["storages"])
    async def delete_photo(sid: uuid.UUID, svc: Service = Svc):
        return await svc.delete_storage_photo(sid)

    @app.get("/storages/{sid}/photo", tags=["storages"])
    async def get_photo(sid: uuid.UUID, svc: Service = Svc):
        return Response(await svc.storage_photo(sid), media_type="image/jpeg")

    # ---------- папки ----------

    @app.post("/folders", status_code=201, tags=["folders"])
    async def create_folder(body: FolderIn, svc: Service = Svc):
        return await svc.create_folder(body.path)

    @app.get("/folders", tags=["folders"])
    async def list_folder(path: str, svc: Service = Svc):
        return await svc.list_folder(path)

    @app.delete("/folders", tags=["folders"])
    async def delete_folder(path: str, svc: Service = Svc):
        return await svc.delete_folder(path)

    # ---------- загрузки ----------

    @app.post("/uploads", status_code=201, tags=["uploads"])
    async def create_upload(body: UploadIn, svc: Service = Svc):
        return await svc.create_upload(body.path, body.size, body.send_as, body.on_conflict, body.callback_url)

    @app.put("/uploads/{tid}", status_code=202, tags=["uploads"], description="тело запроса — содержимое файла")
    async def receive_upload(tid: uuid.UUID, request: Request, svc: Service = Svc):
        return await svc.receive_upload(tid, request.stream())

    @app.post("/uploads/from-url", status_code=202, tags=["uploads"])
    async def create_url_upload(body: UrlUploadIn, svc: Service = Svc):
        return await svc.create_url_upload(body.url, body.path, body.send_as, body.on_conflict, body.callback_url)

    @app.get("/tasks/{tid}", tags=["uploads"])
    async def get_task(tid: uuid.UUID, svc: Service = Svc):
        return await svc.get_task(tid)

    # ---------- файлы ----------

    @app.get("/files/by-path", tags=["files"])
    async def file_by_path(path: str, svc: Service = Svc):
        return await svc.get_file_by_path(path)

    @app.get("/files/{fid}", tags=["files"])
    async def get_file(fid: uuid.UUID, svc: Service = Svc):
        return await svc.get_file(fid)

    @app.get("/files/{fid}/download", tags=["files"])
    async def download(fid: uuid.UUID, range: str | None = Header(None), svc: Service = Svc):
        f, tg_media = await svc.open_download(fid)
        start, end = parse_range(range, f.size) if range else (0, f.size - 1)
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(f.name)}",
        }
        if range:
            headers["Content-Range"] = f"bytes {start}-{end}/{f.size}"
        return StreamingResponse(
            svc.tg.iter_media(tg_media, start, end - start + 1),
            status_code=206 if range else 200, media_type=f.mime, headers=headers,
        )

    @app.delete("/files/{fid}", status_code=204, tags=["files"])
    async def delete_file(fid: uuid.UUID, svc: Service = Svc):
        await svc.delete_file(fid)

    return app
