from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.factory.runtime import get_runtime
from src.application.services import HealthService
from src.infrastructure.background import AppRuntime
from src.infrastructure.database.session import get_session


def get_service_health(
    session: AsyncSession = Depends(get_session),
    runtime: AppRuntime = Depends(get_runtime),
) -> HealthService:
    return HealthService(session=session, runtime=runtime)
