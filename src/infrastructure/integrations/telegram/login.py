import asyncio
import os
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient as TelethonClient

from src.infrastructure.integrations.telegram.client import translate_errors
from src.infrastructure.integrations.telegram.exception import (
    TelegramLoginExpiredError,
)


def session_file(session: Path) -> Path:
    if session.suffix == ".session":
        return session
    return session.with_name(f"{session.name}.session")


class TelegramLogin:
    def __init__(
        self, session: Path, api_id: int, api_hash: str, device_model: str
    ) -> None:
        self._target = session_file(session)
        self._temp = self._target.with_name(f"login-{self._target.name}")
        self._target.parent.mkdir(parents=True, exist_ok=True)
        self._temp.unlink(missing_ok=True)
        self._client = TelethonClient(
            str(self._temp), api_id, api_hash, device_model=device_model
        )
        self._qr = None
        self._phone: str | None = None

    @translate_errors
    async def qr(self) -> tuple[str, datetime]:
        await self._client.connect()
        self._qr = await self._client.qr_login()
        return self._qr.url, self._qr.expires

    @translate_errors
    async def wait_qr(self) -> None:
        try:
            await self._qr.wait()
        except asyncio.TimeoutError as error:
            raise TelegramLoginExpiredError(
                "the QR code expired, request a new one"
            ) from error

    @translate_errors
    async def send_code(self, phone: str) -> None:
        await self._client.connect()
        await self._client.send_code_request(phone)
        self._phone = phone

    @translate_errors
    async def sign_in(self, code: str) -> None:
        await self._client.sign_in(self._phone, code)

    @translate_errors
    async def password(self, password: str) -> None:
        await self._client.sign_in(password=password)

    async def finish(self) -> None:
        await self._client.disconnect()
        os.chmod(self._temp, 0o600)
        os.replace(self._temp, self._target)

    async def close(self) -> None:
        await self._client.disconnect()
        self._temp.unlink(missing_ok=True)
