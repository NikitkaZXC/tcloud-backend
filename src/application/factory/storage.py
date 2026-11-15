from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.factory.runtime import get_runtime
from src.application.services import FileService, FolderService, StorageService
from src.infrastructure.background import BackgroundRuntime
from src.infrastructure.database.session import get_session


def get_service_storage(
    session: AsyncSession = Depends(get_session),
    runtime: BackgroundRuntime = Depends(get_runtime),
) -> StorageService:
    return StorageService(session=session, runtime=runtime)


def get_service_folder(
    session: AsyncSession = Depends(get_session),
    runtime: BackgroundRuntime = Depends(get_runtime),
    storage_service: StorageService = Depends(get_service_storage),
) -> FolderService:
    return FolderService(
        session=session, runtime=runtime, storage_service=storage_service
    )


def get_service_file(
    session: AsyncSession = Depends(get_session),
    runtime: BackgroundRuntime = Depends(get_runtime),
    storage_service: StorageService = Depends(get_service_storage),
) -> FileService:
    return FileService(
        session=session, runtime=runtime, storage_service=storage_service
    )
