from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import StreamingResponse

from src.api.schemas import FileResponse
from src.application.factory import get_runtime, get_service_file
from src.application.services import FileService
from src.infrastructure.background import AppRuntime
from src.infrastructure.enums import MediaKind
from src.utilities.parsers import parse_range

router = APIRouter(prefix="/files", tags=["Files"])


@router.get(
    path="/by-path",
    name="files:by-path",
    summary="File metadata by path",
    status_code=status.HTTP_200_OK,
)
async def get_file_by_path(
    path: str, file_service: FileService = Depends(get_service_file)
) -> FileResponse:
    return await file_service.get_by_path(path)


@router.get(
    path="/{file_id}",
    name="files:get",
    summary="File metadata",
    status_code=status.HTTP_200_OK,
)
async def get_file(
    file_id: UUID, file_service: FileService = Depends(get_service_file)
) -> FileResponse:
    return await file_service.get(file_id)


@router.get(
    path="/{file_id}/download",
    name="files:download",
    summary="Stream the file from Telegram (Range supported)",
    status_code=status.HTTP_200_OK,
    response_class=StreamingResponse,
)
async def download_file(
    file_id: UUID,
    range_header: str | None = Header(None, alias="range"),
    file_service: FileService = Depends(get_service_file),
    runtime: AppRuntime = Depends(get_runtime),
) -> StreamingResponse:
    file, media = await file_service.open_download(file_id)
    start, end = (
        parse_range(range_header, file.size) if range_header else (0, file.size - 1)
    )
    document = file.kind == MediaKind.DOCUMENT
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Disposition": f"{'attachment' if document else 'inline'}; filename*=UTF-8''{quote(file.name)}",
        "X-Content-Type-Options": "nosniff",
    }
    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{file.size}"
    return StreamingResponse(
        runtime.telegram.iter_media(media, start, end - start + 1),
        status_code=(
            status.HTTP_206_PARTIAL_CONTENT if range_header else status.HTTP_200_OK
        ),
        media_type="application/octet-stream" if document else file.mime,
        headers=headers,
    )


@router.delete(
    path="/{file_id}",
    name="files:delete",
    summary="Delete the file from Telegram and the database",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_file(
    file_id: UUID, file_service: FileService = Depends(get_service_file)
) -> None:
    await file_service.delete(file_id)
