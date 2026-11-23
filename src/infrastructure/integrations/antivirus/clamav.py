import asyncio
import struct
import time
from collections.abc import Callable
from pathlib import Path

from src.infrastructure.integrations.antivirus.exception import (
    InfectedFileError,
    ScannerUnavailableError,
    UnscannableFileError,
)

CHUNK = 1024 * 1024
UNSCANNABLE = (
    "Heuristics.Encrypted.",
    "Heuristics.Limits.Exceeded",
    "Heuristics.OLE2.ContainsMacros",
)
UNAVAILABLE = "antivirus is not available, upload rejected"
PING_TTL = 5.0


class ClamAVClient:
    _pinged: tuple[float, bool] | None = None

    def __init__(self, host: str, port: int, timeout: float = 900) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    async def _command(
        self,
        command: bytes,
        path: Path | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> str:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), 10
            )
        except OSError, TimeoutError:
            raise ScannerUnavailableError(UNAVAILABLE) from None
        try:
            writer.write(command)
            if path:
                done = 0
                with open(path, "rb") as file:
                    while chunk := file.read(CHUNK):
                        writer.write(struct.pack(">I", len(chunk)) + chunk)
                        await writer.drain()
                        done += len(chunk)
                        if progress:
                            progress(done)
                writer.write(struct.pack(">I", 0))
            await writer.drain()
            reply = await asyncio.wait_for(reader.read(), self.timeout)
        except OSError, TimeoutError:
            raise ScannerUnavailableError(UNAVAILABLE) from None
        finally:
            writer.close()
        return reply.rstrip(b"\0").decode(errors="replace").strip()

    async def ping(self) -> bool:
        if self._pinged and time.monotonic() - self._pinged[0] < PING_TTL:
            return self._pinged[1]
        try:
            alive = await self._command(b"zPING\0") == "PONG"
        except ScannerUnavailableError:
            alive = False
        self._pinged = (time.monotonic(), alive)
        return alive

    async def scan(
        self, path: Path, progress: Callable[[int], None] | None = None
    ) -> None:
        reply = await self._command(b"zINSTREAM\0", path, progress)
        if reply.endswith("FOUND"):
            virus = reply.removeprefix("stream:").removesuffix("FOUND").strip()
            if virus.startswith(UNSCANNABLE):
                raise UnscannableFileError(
                    f"antivirus cannot check this file fully ({virus})", reason=virus
                )
            raise InfectedFileError(f"antivirus found {virus}, file rejected")
        if not reply.endswith("OK"):
            raise ScannerUnavailableError(UNAVAILABLE)
