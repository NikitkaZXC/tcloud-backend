from fastapi import APIRouter, Depends, status

from src.api.dependencies import get_actor
from src.api.schemas import SettingsResponse, SettingsUpdate
from src.application.factory import get_service_settings
from src.application.schemas import Actor
from src.application.services import SettingsService

router = APIRouter(prefix="/account/settings", tags=["Settings"])


@router.get(
    path="",
    name="settings:get",
    summary="Settings of the current user with defaults filled in",
    status_code=status.HTTP_200_OK,
)
async def get_settings(
    actor: Actor = Depends(get_actor),
    settings_service: SettingsService = Depends(get_service_settings),
) -> SettingsResponse:
    return await settings_service.get(actor)


@router.patch(
    path="",
    name="settings:update",
    summary="Change some settings: only the keys passed are changed",
    status_code=status.HTTP_200_OK,
)
async def update_settings(
    data: SettingsUpdate,
    actor: Actor = Depends(get_actor),
    settings_service: SettingsService = Depends(get_service_settings),
) -> SettingsResponse:
    return await settings_service.update(actor, data)


@router.delete(
    path="",
    name="settings:reset",
    summary="Reset all settings to defaults",
    status_code=status.HTTP_200_OK,
)
async def reset_settings(
    actor: Actor = Depends(get_actor),
    settings_service: SettingsService = Depends(get_service_settings),
) -> SettingsResponse:
    return await settings_service.reset(actor)
