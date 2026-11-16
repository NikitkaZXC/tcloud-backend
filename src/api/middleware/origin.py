from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from src.api.exception.handlers import error_response
from src.config import settings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def allowed(origin: str, host: str | None) -> bool:
    return urlsplit(origin).netloc == host or origin in settings.app.ALLOWED_ORIGINS


class OriginCheckMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is None or allowed(origin, headers.get("host")):
            await self.app(scope, receive, send)
            return
        response = error_response(
            403,
            {"code": "cross_site_request", "message": "cross-site request rejected"},
        )
        await response(scope, receive, send)
