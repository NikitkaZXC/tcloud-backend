from pydantic import BaseModel

from src.api.schemas.base import RequestBody
from src.application.schemas import (
    AuthorizationServiceRead,
    AuthServiceRead,
    QrServiceRead,
    SessionStateServiceRead,
)
from src.utilities.formatters import LoginCode, Password, PhoneNumber


class CodeStart(RequestBody):
    phone: PhoneNumber


class CodeSignIn(RequestBody):
    code: LoginCode


class PasswordSignIn(RequestBody):
    password: Password


class AuthResponse(AuthServiceRead):
    pass


class SessionStateResponse(SessionStateServiceRead):
    pass


class AuthorizationResponse(AuthorizationServiceRead):
    pass


class QrResponse(QrServiceRead):
    pass
