from fastapi import APIRouter, Depends, status

from src.api.schemas import (
    AuthorizationResponse,
    AuthResponse,
    CodeSignIn,
    CodeStart,
    PasswordSignIn,
    QrResponse,
    SessionStateResponse,
)
from src.application.factory import get_service_auth
from src.application.services import AuthService
from src.infrastructure.enums import TelegramSession

router = APIRouter(prefix="/telegram", tags=["Telegram"])


@router.get(
    path="",
    name="telegram:status",
    summary="Which Telegram sessions are logged in and how their logins go",
    status_code=status.HTTP_200_OK,
)
async def auth_status(
    auth_service: AuthService = Depends(get_service_auth),
) -> AuthResponse:
    return auth_service.status()


@router.post(
    path="/{session}/qr",
    name="telegram:qr",
    summary="Start a QR login: render url as a QR code, scan it in Telegram → Settings → Devices",
    status_code=status.HTTP_200_OK,
)
async def login_qr(
    session: TelegramSession, auth_service: AuthService = Depends(get_service_auth)
) -> QrResponse:
    return await auth_service.start_qr(session)


@router.post(
    path="/{session}/code",
    name="telegram:code",
    summary="Start a login by phone: Telegram sends a code to your app",
    status_code=status.HTTP_200_OK,
)
async def login_code(
    session: TelegramSession,
    data: CodeStart,
    auth_service: AuthService = Depends(get_service_auth),
) -> SessionStateResponse:
    return await auth_service.send_code(session, data.phone)


@router.post(
    path="/{session}/sign-in",
    name="telegram:sign-in",
    summary="Send the login code",
    status_code=status.HTTP_200_OK,
)
async def sign_in(
    session: TelegramSession,
    data: CodeSignIn,
    auth_service: AuthService = Depends(get_service_auth),
) -> SessionStateResponse:
    return await auth_service.sign_in(session, data.code)


@router.post(
    path="/{session}/password",
    name="telegram:password",
    summary="Send the two-step verification password",
    status_code=status.HTTP_200_OK,
)
async def password(
    session: TelegramSession,
    data: PasswordSignIn,
    auth_service: AuthService = Depends(get_service_auth),
) -> SessionStateResponse:
    return await auth_service.password(session, data.password.get_secret_value())


@router.post(
    path="/{session}/logout",
    name="telegram:logout",
    summary="Log out of a session and delete its file",
    status_code=status.HTTP_200_OK,
)
async def logout(
    session: TelegramSession, auth_service: AuthService = Depends(get_service_auth)
) -> SessionStateResponse:
    return await auth_service.logout(session)


@router.get(
    path="/sessions",
    name="telegram:sessions",
    summary="tcloud sessions of the account in Telegram, old ones included",
    status_code=status.HTTP_200_OK,
)
async def get_sessions(
    auth_service: AuthService = Depends(get_service_auth),
) -> list[AuthorizationResponse]:
    return await auth_service.sessions()


@router.delete(
    path="/sessions/{session_id}",
    name="telegram:revoke",
    summary="End an old tcloud session in Telegram",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_session(
    session_id: int, auth_service: AuthService = Depends(get_service_auth)
) -> None:
    await auth_service.revoke(session_id)
