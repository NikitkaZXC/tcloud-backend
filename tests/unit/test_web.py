import asyncio
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.infrastructure.integrations.web import (
    DownloadFailedError,
    DownloadTooLargeError,
    UnsafeUrlError,
)
from src.infrastructure.integrations.web import client as web
from src.infrastructure.media import BlockedFileError
from tests.factories import EXE, PDF, make_image


@pytest.mark.parametrize(
    "ip,ok",
    [
        ("8.8.8.8", True),
        ("2606:4700:4700::1111", True),
        ("127.0.0.1", False),
        ("10.0.0.5", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),
        ("100.64.0.1", False),
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("fc00::1", False),
        ("::ffff:127.0.0.1", False),
        ("::ffff:10.0.0.1", False),
        ("not-an-ip", False),
    ],
)
def test_ip_allowed(ip, ok):
    assert web.ip_allowed(ip) is ok


@pytest.mark.parametrize(
    "url",
    [
        "ftp://x.com/a.jpg",
        "file:///etc/passwd",
        "gopher://x",
        "javascript:alert(1)",
        "http://",
        "not a url",
    ],
)
def test_bad_schemes(url):
    with pytest.raises(UnsafeUrlError):
        web.check_scheme(url)


def _patch_dns(monkeypatch, ips):
    async def fake(self, host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", fake)


async def test_resolve_rejects_if_any_address_private(monkeypatch):
    _patch_dns(monkeypatch, ["8.8.8.8", "10.0.0.1"])
    with pytest.raises(UnsafeUrlError):
        await web.resolve("evil.example", 80)


async def test_resolve_public(monkeypatch):
    _patch_dns(monkeypatch, ["8.8.8.8"])
    assert await web.resolve("ok.example", 80) == "8.8.8.8"


async def test_localhost_blocked(tmp_path):
    with pytest.raises(UnsafeUrlError):
        await web.download(
            "http://127.0.0.1:1/a.jpg", tmp_path / "x", 100, lambda *a: None
        )


class Handler(BaseHTTPRequestHandler):
    routes: dict = {}

    def do_GET(self):
        status, headers, body = self.routes.get(self.path, (404, {}, b""))
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        if "Content-Length" not in headers and not headers.get("X-No-Length"):
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", Handler.routes
    httpd.shutdown()
    Handler.routes.clear()


@pytest.fixture
def allow_local(monkeypatch):
    monkeypatch.setattr(web, "ip_allowed", lambda ip: ip == "127.0.0.1")


async def test_download_ok(server, allow_local, tmp_path):
    base, routes = server
    data = make_image("PNG", (500, 500))
    routes["/a.png"] = (200, {}, data)
    seen = []
    await web.download(
        f"{base}/a.png",
        tmp_path / "x",
        10**7,
        lambda done, total: seen.append((done, total)),
    )
    assert (tmp_path / "x").read_bytes() == data
    assert seen[-1] == (len(data), len(data))


async def test_download_rejects_executable_early(server, allow_local, tmp_path):
    base, routes = server
    routes["/a.pdf"] = (200, {}, EXE + b"\0" * 20000)
    with pytest.raises(BlockedFileError):
        await web.download(f"{base}/a.pdf", tmp_path / "x", 10**7, lambda *a: None)


async def test_download_document(server, allow_local, tmp_path):
    base, routes = server
    routes["/a.pdf"] = (200, {}, PDF)
    await web.download(f"{base}/a.pdf", tmp_path / "x", 10**7, lambda *a: None)
    assert (tmp_path / "x").read_bytes() == PDF


async def test_download_rejects_by_content_length(server, allow_local, tmp_path):
    base, routes = server
    routes["/big.jpg"] = (200, {}, make_image())
    with pytest.raises(DownloadTooLargeError):
        await web.download(f"{base}/big.jpg", tmp_path / "x", 10, lambda *a: None)


async def test_download_limit_without_content_length(server, allow_local, tmp_path):
    base, routes = server
    routes["/a.png"] = (200, {"X-No-Length": "1"}, make_image("PNG", (300, 300)))
    with pytest.raises(DownloadTooLargeError):
        await web.download(f"{base}/a.png", tmp_path / "x", 100, lambda *a: None)


async def test_download_http_error(server, allow_local, tmp_path):
    base, _ = server
    with pytest.raises(DownloadFailedError):
        await web.download(f"{base}/missing.jpg", tmp_path / "x", 100, lambda *a: None)


async def test_redirect_to_private_blocked(server, allow_local, tmp_path):
    base, routes = server
    routes["/r"] = (302, {"Location": "http://[::1]:1/secret.jpg"}, b"")
    with pytest.raises(UnsafeUrlError):
        await web.download(f"{base}/r", tmp_path / "x", 10**6, lambda *a: None)


async def test_redirect_followed(server, allow_local, tmp_path):
    base, routes = server
    routes["/r"] = (302, {"Location": "/a.png"}, b"")
    routes["/a.png"] = (200, {}, make_image("PNG"))
    await web.download(f"{base}/r", tmp_path / "x", 10**6, lambda *a: None)
    assert (tmp_path / "x").read_bytes() == make_image("PNG")


async def test_too_many_redirects(server, allow_local, tmp_path):
    base, routes = server
    routes["/loop"] = (302, {"Location": "/loop"}, b"")
    with pytest.raises(UnsafeUrlError, match="redirects"):
        await web.download(f"{base}/loop", tmp_path / "x", 10**6, lambda *a: None)
