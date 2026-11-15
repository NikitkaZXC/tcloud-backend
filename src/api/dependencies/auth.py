import secrets

from fastapi import Security
from fastapi.security import APIKeyHeader

from src.api.exception import UnauthorizedErrorHTTP
from src.config import settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(key: str | None = Security(api_key_header)) -> None:
    expected = settings.app.API_KEY.get_secret_value().encode()
    if not key or not secrets.compare_digest(key.encode(), expected):
        raise UnauthorizedErrorHTTP
