from __future__ import annotations

import asyncio

from telethon import errors as tg_errors


class TCloudError(Exception):
    """Ошибка, которая уходит клиенту как {"error": {"code", "message", ...}}."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def describe(e: BaseException) -> tuple[int, dict]:
    """Любое исключение → (HTTP-статус, тело ошибки)."""
    if isinstance(e, TCloudError):
        return e.status, {"code": e.code, "message": e.message, **e.extra}
    if isinstance(e, tg_errors.FloodWaitError):
        return 429, {"code": "flood_wait", "message": f"Telegram asks to wait {e.seconds} s", "retry_after": e.seconds}
    if isinstance(e, tg_errors.RPCError):
        return 502, {"code": "telegram_error", "message": str(e)}
    if isinstance(e, asyncio.CancelledError):
        return 500, {"code": "interrupted", "message": "task was interrupted"}
    return 500, {"code": "internal", "message": "internal error"}
