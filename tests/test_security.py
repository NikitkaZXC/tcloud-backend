import asyncio
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tcloud import security
from tcloud.errors import TCloudError
from tests.conftest import image


@pytest.mark.parametrize("ip,ok", [
    ("8.8.8.8", True),
    ("2606:4700:4700::1111", True),
    ("127.0.0.1", False),
    ("10.0.0.5", False),
    ("172.16.0.1", False),
    ("192.168.1.1", False),
    ("169.254.169.254", False),  # метаданные облака
    ("100.64.0.1", False),  # CGNAT
    ("0.0.0.0", False),
    ("224.0.0.1", False),
    ("::1", False),
    ("fe80::1", False),
    ("fc00::1", False),
    ("::ffff:127.0.0.1", False),
    ("::ffff:10.0.0.1", False),
    ("not-an-ip", False),
])
def test_ip_allowed(ip, ok):
    assert security.ip_allowed(ip) is ok


@pytest.mark.parametrize("url", ["ftp://x.com/a.jpg", "file:///etc/passwd", "gopher://x", "javascript:alert(1)", "http://", "not a url"])
def test_bad_schemes(url):
    with pytest.raises(TCloudError) as e:
        security.check_scheme(url)
    assert e.value.code == "unsafe_url"


def _patch_dns(monkeypatch, ips):
    async def fake(self, host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", fake)


def test_resolve_rejects_if_any_address_private(monkeypatch):
    _patch_dns(monkeypatch, ["8.8.8.8", "10.0.0.1"])
    with pytest.raises(TCloudError):
        asyncio.run(security._resolve("evil.example", 80))


def test_resolve_public(monkeypatch):
    _patch_dns(monkeypatch, ["8.8.8.8"])
    assert asyncio.run(security._resolve("ok.example", 80)) == "8.8.8.8"


def test_localhost_blocked():
    with pytest.raises(TCloudError) as e:
        asyncio.run(security.download("http://127.0.0.1:1/a.jpg", None, 100, lambda *a: None))
    assert e.value.code == "unsafe_url"


# ---------- реальный HTTP-сервер на localhost (проверку IP для него отключаем) ----------

class Handler(BaseHTTPRequestHandler):
    routes: dict = {}

    def do_GET(self):
        status, headers, body = self.routes.get(self.path, (404, {}, b""))
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        if "Content-Length" not in headers:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def http_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", Handler.routes
    srv.shutdown()
    Handler.routes.clear()


def test_download_ok(http_server, monkeypatch, tmp_path):
    monkeypatch.setattr(security, "ip_allowed", lambda ip: ip == "127.0.0.1")
    base, routes = http_server
    data = image("PNG", (500, 500))
    routes["/a.png"] = (200, {}, data)
    seen = []
    asyncio.run(security.download(f"{base}/a.png", tmp_path / "x", 10**7, lambda d, t: seen.append((d, t))))
    assert (tmp_path / "x").read_bytes() == data
    assert seen[-1] == (len(data), len(data))


def test_download_rejects_non_media_early(http_server, monkeypatch, tmp_path):
    monkeypatch.setattr(security, "ip_allowed", lambda ip: True)
    base, routes = http_server
    routes["/a.jpg"] = (200, {}, b"MZ" + b"\0" * 20000)
    with pytest.raises(TCloudError) as e:
        asyncio.run(security.download(f"{base}/a.jpg", tmp_path / "x", 10**7, lambda *a: None))
    assert e.value.status == 415


def test_download_rejects_by_content_length(http_server, monkeypatch, tmp_path):
    monkeypatch.setattr(security, "ip_allowed", lambda ip: True)
    base, routes = http_server
    routes["/big.jpg"] = (200, {}, image())
    with pytest.raises(TCloudError) as e:
        asyncio.run(security.download(f"{base}/big.jpg", tmp_path / "x", 10, lambda *a: None))
    assert e.value.status == 413


def test_download_limit_without_content_length(http_server, monkeypatch, tmp_path):
    monkeypatch.setattr(security, "ip_allowed", lambda ip: True)
    base, routes = http_server
    data = image("PNG", (300, 300))
    routes["/a.png"] = (200, {"Content-Length": "0"}, data)  # врёт про размер
    with pytest.raises(TCloudError):
        asyncio.run(security.download(f"{base}/a.png", tmp_path / "x", 100, lambda *a: None))


def test_download_http_error(http_server, monkeypatch, tmp_path):
    monkeypatch.setattr(security, "ip_allowed", lambda ip: True)
    base, _ = http_server
    with pytest.raises(TCloudError) as e:
        asyncio.run(security.download(f"{base}/missing.jpg", tmp_path / "x", 100, lambda *a: None))
    assert e.value.code == "download_failed"


def test_redirect_to_private_blocked(http_server, monkeypatch, tmp_path):
    monkeypatch.setattr(security, "ip_allowed", lambda ip: ip == "127.0.0.1")
    base, routes = http_server
    routes["/r"] = (302, {"Location": "http://[::1]:1/secret.jpg"}, b"")
    with pytest.raises(TCloudError) as e:
        asyncio.run(security.download(f"{base}/r", tmp_path / "x", 10**6, lambda *a: None))
    assert e.value.code == "unsafe_url"


def test_redirect_followed(http_server, monkeypatch, tmp_path):
    monkeypatch.setattr(security, "ip_allowed", lambda ip: True)
    base, routes = http_server
    routes["/r"] = (302, {"Location": "/a.png"}, b"")
    routes["/a.png"] = (200, {}, image("PNG"))
    asyncio.run(security.download(f"{base}/r", tmp_path / "x", 10**6, lambda *a: None))
    assert (tmp_path / "x").read_bytes() == image("PNG")


def test_too_many_redirects(http_server, monkeypatch, tmp_path):
    monkeypatch.setattr(security, "ip_allowed", lambda ip: True)
    base, routes = http_server
    routes["/loop"] = (302, {"Location": "/loop"}, b"")
    with pytest.raises(TCloudError) as e:
        asyncio.run(security.download(f"{base}/loop", tmp_path / "x", 10**6, lambda *a: None))
    assert "redirects" in e.value.message


# ---------- ClamAV: фейковый clamd по настоящему протоколу ----------

async def _fake_clamd(reader, writer):
    cmd = await reader.readuntil(b"\0")
    if cmd == b"zPING\0":
        writer.write(b"PONG\0")
    else:
        data = b""
        while size := struct.unpack(">I", await reader.readexactly(4))[0]:
            data += await reader.readexactly(size)
        writer.write(b"stream: Eicar-Signature FOUND\0" if b"EICAR" in data else b"stream: OK\0")
    await writer.drain()
    writer.close()


def _with_clamd(coro_factory):
    async def run():
        srv = await asyncio.start_server(_fake_clamd, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            return await coro_factory(security.ClamAV("127.0.0.1", port))

    return asyncio.run(run())


def test_clamav_clean(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"x" * (3 * security.CHUNK + 5))
    _with_clamd(lambda av: av.scan(p))


def test_clamav_infected(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"X5O!P%@AP EICAR test")
    with pytest.raises(TCloudError) as e:
        _with_clamd(lambda av: av.scan(p))
    assert e.value.code == "infected" and "Eicar" in e.value.message


def test_clamav_ping():
    assert _with_clamd(lambda av: av.ping()) is True


def test_clamav_unavailable_fails_closed(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"x")
    av = security.ClamAV("127.0.0.1", 1)
    with pytest.raises(TCloudError) as e:
        asyncio.run(av.scan(p))
    assert e.value.code == "scanner_unavailable"
    assert asyncio.run(av.ping()) is False
