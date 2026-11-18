from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services import SettingsService
from src.infrastructure.database.session import get_session


def get_service_settings(
    session: AsyncSession = Depends(get_session),
) -> SettingsService:
    return SettingsService(session=session)
