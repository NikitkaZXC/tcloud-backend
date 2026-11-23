from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import Response, StreamingResponse

from src.api.dependencies import get_actor, get_root, rate_limit_by_user
from src.api.schemas import (
    ActivityResponse,
    FileResponse,
    FileSearchParams,
    FileSearchResponse,
    MimeCountResponse,
    PreviewsClearResponse,
)
from src.application.factory import get_runtime, get_service_file
from src.application.schemas import Actor, FileSearchServiceCreate
from src.application.services import FileService
from src.application.services.file import file_body, tracked
from src.config import settings
from src.infrastructure.background import AppRuntime
from src.infrastructure.enums import MediaKind
from src.utilities.parsers import parse_range

THUMB_MAX_AGE = 24 * 3600

router = APIRouter(prefix="/files", tags=["Files"])
stream_router = APIRouter(
    prefix="/files",
    tags=["Files"],
    dependencies=[
        Depends(rate_limit_by_user("file_read", settings.rate_limit.FILE_READ_USER))
    ],
)


@router.get(
    path="/search",
    name="files:search",
    summary="Search readable files by name and filters in a folder subtree, a storage or everywhere",
    status_code=status.HTTP_200_OK,
)
async def search_files(
    params: Annotated[FileSearchParams, Query()],
    actor: Actor = Depends(get_actor),
    file_service: FileService = Depends(get_service_file),
) -> FileSearchResponse:
    return await file_service.search(
        actor, FileSearchServiceCreate.model_validate(params.model_dump())
    )


@router.get(
    path="/search/mimes",
    name="files:search-mimes",
    summary="MIME types with file counts among readable files of the scope",
    status_code=status.HTTP_200_OK,
)
async def search_mimes(
    path: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    actor: Actor = Depends(get_actor),
    file_service: FileService = Depends(get_service_file),
) -> list[MimeCountResponse]:
    return await file_service.mime_counts(actor, path)


@router.get(
    path="/activity",
    name="files:activity",
    summary="Files uploaded per UTC day over the last days among readable files of the scope",
    status_code=status.HTTP_200_OK,
)
async def get_activity(
    path: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
    days: Annotated[int, Query(ge=1, le=366)] = 365,
    actor: Actor = Depends(get_actor),
    file_service: FileService = Depends(get_service_file),
) -> ActivityResponse:
    return await file_service.activity(actor, path, days)


@router.get(
    path="/by-path",
    name="files:by-path",
    summary="File metadata by path",
    status_code=status.HTTP_200_OK,
)
async def get_file_by_path(
    path: str,
    actor: Actor = Depends(get_actor),
    file_service: FileService = Depends(get_service_file),
) -> FileResponse:
    return await file_service.get_by_path(actor, path)


@router.get(
    path="/{file_id}",
    name="files:get",
    summary="File metadata",
    status_code=status.HTTP_200_OK,
)
async def get_file(
    file_id: UUID,
    actor: Actor = Depends(get_actor),
    file_service: FileService = Depends(get_service_file),
) -> FileResponse:
    return await file_service.get(actor, file_id)


@stream_router.get(
    path="/{file_id}/download",
    name="files:download",
    summary="Stream the file from Telegram (Range supported)",
    status_code=status.HTTP_200_OK,
    response_class=StreamingResponse,
)
async def download_file(
    file_id: UUID,
    range_header: str | None = Header(None, alias="range"),
    actor: Actor = Depends(get_actor),
    file_service: FileService = Depends(get_service_file),
    runtime: AppRuntime = Depends(get_runtime),
) -> StreamingResponse:
    file, media = await file_service.open_download(actor, file_id)
    start, end = (
        parse_range(range_header, file.size) if range_header else (0, file.size - 1)
    )
    document = file.kind == MediaKind.DOCUMENT
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": f"{'attachment' if document else 'inline'}; filename*=UTF-8''{quote(file.name)}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "Cache-Control": "no-transform",
    }
    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{file.size}"
    return StreamingResponse(
        tracked(
            file_body(runtime.download, media, file, start, end),
            runtime.store,
            file.storage_id,
            "read",
        ),
        status_code=(
            status.HTTP_206_PARTIAL_CONTENT if range_header else status.HTTP_200_OK
        ),
        media_type="application/octet-stream" if document else file.mime,
        headers=headers,
    )


@stream_router.get(
    path="/{file_id}/thumb",
    name="files:thumb",
    summary="Small JPEG preview: Telegram's for open files, our own encrypted one for encrypted files",
    status_code=status.HTTP_200_OK,
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}, 304: {}},
)
async def get_thumbnail(
    file_id: UUID,
    if_none_match: str | None = Header(None, alias="if-none-match"),
    actor: Actor = Depends(get_actor),
    file_service: FileService = Depends(get_service_file),
) -> Response:
    tag, data = await file_service.get_thumbnail(actor, file_id, if_none_match)
    headers = {
        "ETag": tag,
        "Cache-Control": f"private, max-age={THUMB_MAX_AGE}",
        "X-Content-Type-Options": "nosniff",
    }
    if data is None:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(data, media_type="image/jpeg", headers=headers)


@router.post(
    path="/previews/backfill",
    dependencies=[
        Depends(
            rate_limit_by_user(
                "previews_backfill", settings.rate_limit.PREVIEWS_BACKFILL_USER
            )
        )
    ],
    name="files:previews-backfill",
    summary="Root: queue a background job that builds missing previews of encrypted photos, GIFs and videos",
    status_code=status.HTTP_202_ACCEPTED,
)
async def backfill_previews(
    actor: Actor = Depends(get_root),
    file_service: FileService = Depends(get_service_file),
) -> None:
    await file_service.backfill_previews(actor)


@router.delete(
    path="/previews",
    dependencies=[
        Depends(
            rate_limit_by_user(
                "previews_clear", settings.rate_limit.PREVIEWS_CLEAR_USER
            )
        )
    ],
    name="files:previews-clear",
    summary="Root: delete every stored preview of encrypted files, the files stay",
    status_code=status.HTTP_200_OK,
)
async def clear_previews(
    actor: Actor = Depends(get_root),
    file_service: FileService = Depends(get_service_file),
) -> PreviewsClearResponse:
    return await file_service.clear_previews(actor)


@router.delete(
    path="/{file_id}",
    name="files:delete",
    summary="Delete the file from Telegram and the database",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_file(
    file_id: UUID,
    actor: Actor = Depends(get_actor),
    file_service: FileService = Depends(get_service_file),
) -> None:
    await file_service.delete(actor, file_id)
