import asyncio
import struct
from pathlib import Path

import pytest

from src.infrastructure.integrations.antivirus import (
    ClamAVClient,
    InfectedFileError,
    ScannerUnavailableError,
    UnscannableFileError,
)
from src.infrastructure.integrations.antivirus.clamav import CHUNK

ROOT = Path(__file__).parents[2]


REPLIES = {
    b"EICAR": b"Eicar-Signature FOUND",
    b"ENCRYPTED": b"Heuristics.Encrypted.Zip FOUND",
    b"PDFLOCK": b"Heuristics.Encrypted.PDF FOUND",
    b"MANY": b"Heuristics.Limits.Exceeded.MaxFiles FOUND",
    b"MACRO": b"Heuristics.OLE2.ContainsMacros.VBA FOUND",
    b"PUA": b"PUA.Win.Tool.Agent FOUND",
    b"BROKEN": b"Heuristics.Broken.Executable FOUND",
}


async def _fake_clamd(reader, writer):
    command = await reader.readuntil(b"\0")
    if command == b"zPING\0":
        writer.write(b"PONG\0")
    else:
        data = b""
        while size := struct.unpack(">I", await reader.readexactly(4))[0]:
            data += await reader.readexactly(size)
        reply = next(
            (reply for marker, reply in REPLIES.items() if marker in data), b"OK"
        )
        writer.write(b"stream: " + reply + b"\0")
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


@pytest.mark.parametrize("marker", [b"ENCRYPTED", b"PDFLOCK", b"MANY", b"MACRO"])
async def test_unscannable(clamav, tmp_path, marker):
    target = tmp_path / "f"
    target.write_bytes(marker)
    with pytest.raises(UnscannableFileError) as error:
        await clamav.scan(target)
    assert error.value.extra["reason"].startswith("Heuristics.")


@pytest.mark.parametrize("marker", [b"PUA", b"BROKEN"])
async def test_heuristic_detections_are_infected(clamav, tmp_path, marker):
    target = tmp_path / "f"
    target.write_bytes(marker)
    with pytest.raises(InfectedFileError):
        await clamav.scan(target)


def test_clamd_config_is_strict():
    config = dict(
        line.split(maxsplit=1)
        for line in (ROOT / "docker" / "clamd.conf").read_text().splitlines()
        if line and not line.startswith("#")
    )
    for option in (
        "AlertExceedsMax",
        "AlertEncrypted",
        "AlertOLE2Macros",
        "AlertBrokenExecutables",
        "DetectPUA",
        "HeuristicAlerts",
    ):
        assert config[option] == "yes", option
    assert config["HeuristicScanPrecedence"] == "no"
    assert int(config["MaxScanTime"]) >= int(config["ReadTimeout"]) * 1000
