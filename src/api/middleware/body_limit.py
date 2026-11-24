import re

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.api.exception.handlers import error_response
from src.config import settings

STREAMING = (
    ("POST", re.compile(r"/api/uploads")),
    ("PUT", re.compile(r"/api/uploads/sessions/[^/]+")),
    ("PUT", re.compile(r"/api/storages/[^/]+/photo")),
)


def streams_body(method: str, path: str) -> bool:
    return any(
        method == allowed and pattern.fullmatch(path) for allowed, pattern in STREAMING
    )


def too_large(limit: int):
    return error_response(
        413,
        {
            "code": "body_too_large",
            "message": f"request body is larger than {limit} bytes",
            "limit": limit,
        },
    )


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or streams_body(scope["method"], scope["path"]):
            await self.app(scope, receive, send)
            return
        limit = settings.app.MAX_BODY_SIZE
        length = Headers(scope=scope).get("content-length", "")
        if length.isdigit() and int(length) > limit:
            await too_large(limit)(scope, receive, send)
            return
        received, answered = 0, False

        async def limited_receive() -> Message:
            nonlocal received, answered
            if answered:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    answered = True
                    await too_large(limit)(scope, receive, send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not answered:
                await send(message)

        await self.app(scope, limited_receive, guarded_send)
