import asyncio
import struct
from pathlib import Path

from src.infrastructure.integrations.antivirus.exception import (
    InfectedFileError,
    ScannerUnavailableError,
)

CHUNK = 1024 * 1024
UNAVAILABLE = "antivirus is not available, upload rejected"


class ClamAVClient:
    def __init__(self, host: str, port: int, timeout: float = 900) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    async def _command(self, command: bytes, path: Path | None = None) -> str:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), 10
            )
        except OSError, TimeoutError:
            raise ScannerUnavailableError(UNAVAILABLE) from None
        try:
            writer.write(command)
            if path:
                with open(path, "rb") as file:
                    while chunk := file.read(CHUNK):
                        writer.write(struct.pack(">I", len(chunk)) + chunk)
                        await writer.drain()
                writer.write(struct.pack(">I", 0))
            await writer.drain()
            reply = await asyncio.wait_for(reader.read(), self.timeout)
        except OSError, TimeoutError:
            raise ScannerUnavailableError(UNAVAILABLE) from None
        finally:
            writer.close()
        return reply.rstrip(b"\0").decode(errors="replace").strip()

    async def ping(self) -> bool:
        try:
            return await self._command(b"zPING\0") == "PONG"
        except ScannerUnavailableError:
            return False

    async def scan(self, path: Path) -> None:
        reply = await self._command(b"zINSTREAM\0", path)
        if reply.endswith("FOUND"):
            virus = reply.removeprefix("stream:").removesuffix("FOUND").strip()
            raise InfectedFileError(f"antivirus found {virus}, file rejected")
        if not reply.endswith("OK"):
            raise ScannerUnavailableError(UNAVAILABLE)
