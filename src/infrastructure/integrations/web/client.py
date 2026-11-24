import asyncio
import ipaddress
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from src.infrastructure.integrations.web.exception import (
    DownloadFailedError,
    DownloadSizeMismatchError,
    DownloadTooLargeError,
    UnsafeUrlError,
)

CHUNK = 1024 * 1024
MAX_REDIRECTS = 5
HTTP_TIMEOUT = httpx.Timeout(30.0, read=60.0)


def ip_allowed(ip: str) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def check_scheme(url: str) -> httpx.URL:
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL:
        raise UnsafeUrlError("invalid url") from None
    if parsed.scheme not in ("http", "https") or not parsed.host:
        raise UnsafeUrlError("only http(s) urls are allowed")
    return parsed


def check_webhook(url: str) -> None:
    host = check_scheme(url).host
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return
    if not ip_allowed(host):
        raise UnsafeUrlError("url points to a private or reserved address")


async def resolve(host: str, port: int) -> str:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except socket.gaierror:
        raise UnsafeUrlError(f"cannot resolve {host}") from None
    ips = [info[4][0] for info in infos]
    if not ips or not all(ip_allowed(ip) for ip in ips):
        raise UnsafeUrlError("url points to a private or reserved address")
    return ips[0]


@asynccontextmanager
async def open_url(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    content: bytes | None = None,
) -> AsyncIterator[httpx.Response]:
    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT, follow_redirects=False, trust_env=False
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            parsed = check_scheme(url)
            host = parsed.raw_host.decode("ascii")
            ip = await resolve(
                host, parsed.port or (443 if parsed.scheme == "https" else 80)
            )
            request = client.build_request(
                method,
                parsed.copy_with(host=ip),
                content=content,
                headers={**(headers or {}), "Host": parsed.netloc.decode("ascii")},
                extensions={"sni_hostname": host},
            )
            response = await client.send(request, stream=True)
            if not response.is_redirect:
                break
            await response.aclose()
            url = str(parsed.join(response.headers["location"]))
        else:
            raise UnsafeUrlError("too many redirects")
        try:
            yield response
        finally:
            await response.aclose()


async def download(
    url: str,
    target: Path,
    limit: int,
    progress: Callable[[int, int | None], None],
    expected: int | None = None,
) -> None:
    async with open_url(url) as response:
        if response.status_code != 200:
            raise DownloadFailedError(f"remote server answered {response.status_code}")
        try:
            total = int(response.headers.get("content-length", "")) or None
        except ValueError:
            total = None
        if expected and total and total != expected:
            raise DownloadSizeMismatchError(
                f"declared {expected} bytes, remote file is {total}"
            )
        if expected:
            limit = min(limit, expected)
        if total and total > limit:
            raise DownloadTooLargeError(
                f"file is {total} bytes, limit is {limit}", limit=limit
            )
        done = 0
        with open(target, "wb") as file:
            async for chunk in response.aiter_bytes(CHUNK):
                done += len(chunk)
                if done > limit:
                    raise DownloadTooLargeError(
                        f"file is larger than {limit} bytes", limit=limit
                    )
                file.write(chunk)
                progress(done, total)
        if expected and done != expected:
            raise DownloadSizeMismatchError(
                f"declared {expected} bytes, received {done}"
            )
