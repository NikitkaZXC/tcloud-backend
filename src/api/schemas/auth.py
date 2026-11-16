from pydantic import BaseModel

from src.application.schemas import (
    AuthorizationServiceRead,
    AuthServiceRead,
    SessionStateServiceRead,
)
from src.utilities.formatters import LoginCode, Password, PhoneNumber


class CodeStart(BaseModel):
    phone: PhoneNumber


class CodeSignIn(BaseModel):
    code: LoginCode


class PasswordSignIn(BaseModel):
    password: Password


class AuthResponse(AuthServiceRead):
    pass


class SessionStateResponse(SessionStateServiceRead):
    pass


class AuthorizationResponse(AuthorizationServiceRead):
    pass
