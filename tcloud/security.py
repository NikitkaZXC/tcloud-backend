"""Антивирус и безопасные исходящие HTTP-запросы (ТЗ §8)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import struct
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from . import media
from .errors import TCloudError

CHUNK = 1024 * 1024
MAX_REDIRECTS = 5
HTTP_TIMEOUT = httpx.Timeout(30.0, read=60.0)


class ClamAV:
    """Клиент clamd по TCP (протокол INSTREAM). Недоступен — загрузка отклоняется (fail-closed)."""

    def __init__(self, host: str, port: int, timeout: float = 900):
        self.host, self.port, self.timeout = host, port, timeout

    def _unavailable(self) -> TCloudError:
        return TCloudError(503, "scanner_unavailable", "antivirus is not available, upload rejected")

    async def _command(self, cmd: bytes, path: Path | None = None) -> str:
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 10)
        except (OSError, TimeoutError):
            raise self._unavailable() from None
        try:
            writer.write(cmd)
            if path:
                with open(path, "rb") as f:
                    while chunk := f.read(CHUNK):
                        writer.write(struct.pack(">I", len(chunk)) + chunk)
                        await writer.drain()
                writer.write(struct.pack(">I", 0))
            await writer.drain()
            reply = await asyncio.wait_for(reader.read(), self.timeout)
        except (OSError, TimeoutError):
            raise self._unavailable() from None
        finally:
            writer.close()
        return reply.rstrip(b"\0").decode(errors="replace").strip()

    async def ping(self) -> bool:
        try:
            return await self._command(b"zPING\0") == "PONG"
        except TCloudError:
            return False

    async def scan(self, path: Path) -> None:
        reply = await self._command(b"zINSTREAM\0", path)
        if reply.endswith("FOUND"):
            virus = reply.removeprefix("stream:").removesuffix("FOUND").strip()
            raise TCloudError(422, "infected", f"antivirus found {virus}, file rejected")
        if not reply.endswith("OK"):
            raise self._unavailable()


def ip_allowed(ip: str) -> bool:
    """Только публичные адреса: никаких localhost, LAN, link-local, метаданных облака."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def _unsafe(message: str) -> TCloudError:
    return TCloudError(422, "unsafe_url", message)


def check_scheme(url: str) -> httpx.URL:
    try:
        u = httpx.URL(url)
    except httpx.InvalidURL:
        raise _unsafe("invalid url") from None
    if u.scheme not in ("http", "https") or not u.host:
        raise _unsafe("only http(s) urls are allowed")
    return u


async def _resolve(host: str, port: int) -> str:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise _unsafe(f"cannot resolve {host}") from None
    ips = [i[4][0] for i in infos]
    # Все адреса должны быть публичными, иначе DNS может подсунуть внутренний
    if not ips or not all(ip_allowed(ip) for ip in ips):
        raise _unsafe("url points to a private or reserved address")
    return ips[0]


@asynccontextmanager
async def open_url(url: str, method: str = "GET", headers: dict | None = None, content: bytes | None = None) -> AsyncIterator[httpx.Response]:
    """Запрос к проверенному IP (без повторного DNS), каждый редирект проверяется заново."""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=False, trust_env=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            u = check_scheme(url)
            host = u.raw_host.decode("ascii")
            ip = await _resolve(host, u.port or (443 if u.scheme == "https" else 80))
            req = client.build_request(
                method, u.copy_with(host=ip), content=content,
                headers={**(headers or {}), "Host": u.netloc.decode("ascii")},
                extensions={"sni_hostname": host},
            )
            resp = await client.send(req, stream=True)
            if not resp.is_redirect:
                break
            await resp.aclose()
            url = str(u.join(resp.headers["location"]))
        else:
            raise _unsafe("too many redirects")
        try:
            yield resp
        finally:
            await resp.aclose()


async def download(url: str, dest: Path, limit: int, progress: Callable[[int, int | None], None]) -> None:
    """Скачать по ссылке с лимитом размера; не-медиа обрывается на первых байтах."""
    async with open_url(url) as resp:
        if resp.status_code != 200:
            raise TCloudError(422, "download_failed", f"remote server answered {resp.status_code}")
        try:
            total = int(resp.headers.get("content-length", "")) or None
        except ValueError:
            total = None
        if total and total > limit:
            raise TCloudError(413, "too_large", f"file is {total} bytes, limit is {limit}")
        done, head = 0, b""
        with open(dest, "wb") as f:
            async for chunk in resp.aiter_bytes(CHUNK):
                if len(head) < media.SNIFF_BYTES:
                    head += chunk
                    if len(head) >= media.SNIFF_BYTES:
                        media.sniff(head)
                done += len(chunk)
                if done > limit:
                    raise TCloudError(413, "too_large", f"file is larger than {limit} bytes")
                f.write(chunk)
                progress(done, total)
        if len(head) < media.SNIFF_BYTES:
            media.sniff(head)
