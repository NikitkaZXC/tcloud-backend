from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.factory.runtime import get_runtime
from src.application.services import SettingsService
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.session import get_session


def get_service_settings(
    session: AsyncSession = Depends(get_session),
    runtime: AppRuntime = Depends(get_runtime),
) -> SettingsService:
    return SettingsService(session=session, runtime=runtime)
