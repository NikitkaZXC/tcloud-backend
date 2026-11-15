import asyncio
import struct

import pytest

from src.infrastructure.integrations.antivirus import (
    ClamAVClient,
    InfectedFileError,
    ScannerUnavailableError,
)
from src.infrastructure.integrations.antivirus.clamav import CHUNK


async def _fake_clamd(reader, writer):
    command = await reader.readuntil(b"\0")
    if command == b"zPING\0":
        writer.write(b"PONG\0")
    else:
        data = b""
        while size := struct.unpack(">I", await reader.readexactly(4))[0]:
            data += await reader.readexactly(size)
        writer.write(
            b"stream: Eicar-Signature FOUND\0" if b"EICAR" in data else b"stream: OK\0"
        )
    await writer.drain()
    writer.close()


@pytest.fixture
async def clamav():
    server = await asyncio.start_server(_fake_clamd, "127.0.0.1", 0)
    async with server:
        yield ClamAVClient("127.0.0.1", server.sockets[0].getsockname()[1])


async def test_clean(clamav, tmp_path):
    target = tmp_path / "f"
    target.write_bytes(b"x" * (3 * CHUNK + 5))
    await clamav.scan(target)


async def test_infected(clamav, tmp_path):
    target = tmp_path / "f"
    target.write_bytes(b"X5O!P%@AP EICAR test")
    with pytest.raises(InfectedFileError, match="Eicar"):
        await clamav.scan(target)


async def test_ping(clamav):
    assert await clamav.ping() is True


async def test_unavailable_fails_closed(tmp_path):
    target = tmp_path / "f"
    target.write_bytes(b"x")
    scanner = ClamAVClient("127.0.0.1", 1)
    with pytest.raises(ScannerUnavailableError):
        await scanner.scan(target)
    assert await scanner.ping() is False
