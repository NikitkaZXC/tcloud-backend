from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.factory.runtime import get_runtime
from src.application.factory.storage import get_service_storage
from src.application.services import (
    StorageService,
    TaskService,
    UploadService,
)
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.session import get_session


def get_service_task(
    session: AsyncSession = Depends(get_session),
    runtime: AppRuntime = Depends(get_runtime),
) -> TaskService:
    return TaskService(session=session, store=runtime.store)


def get_service_upload(
    session: AsyncSession = Depends(get_session),
    runtime: AppRuntime = Depends(get_runtime),
    storage_service: StorageService = Depends(get_service_storage),
    task_service: TaskService = Depends(get_service_task),
) -> UploadService:
    return UploadService(
        session=session,
        runtime=runtime,
        storage_service=storage_service,
        task_service=task_service,
    )
