"""Тесты гоняются в Docker: docker compose --profile test run --rm tests (нужны Postgres и ffmpeg)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from telethon import errors

from tcloud.api import create_app
from tcloud.config import Settings
from tcloud.errors import TCloudError
from tcloud.telegram import ChannelInfo, Sent

DB_URL = os.environ["TCLOUD_DATABASE_URL"]
KEY = "test"


class FakeTelegram:
    """Telegram в памяти: каналы, сообщения, байты файлов."""

    def __init__(self):
        self.premium = False
        self.chans: dict[int, dict] = {}
        self.next_cid = 1001234567000
        self.next_mid = 1
        self.flood: int | None = None  # FloodWait на каждую отправку
        self.flood_once: int | None = None  # FloodWait только на первую
        self.fail_times = 0  # столько раз подряд — сетевой сбой
        self.delay = 0.0  # с на отправку: чтобы задачи пересекались по времени
        self.sending = 0
        self.max_sending = 0

    async def start(self): ...
    async def stop(self): ...

    def connected(self):
        return True

    async def is_premium(self):
        return self.premium

    def add_channel(self, title="ch", public=False, admin=True) -> int:
        self.next_cid += 1
        cid = -self.next_cid
        self.chans[cid] = {"title": title, "about": "", "photo": None, "public": public, "admin": admin, "messages": {}}
        return cid

    async def create_channel(self, title, about):
        cid = self.add_channel(title)
        self.chans[cid]["about"] = about
        return cid

    async def check_channel(self, cid):
        ch = self.chans.get(cid)
        if ch is None:
            raise ValueError("not found")
        if ch["public"]:
            raise ValueError("channel is public")
        if not ch["admin"]:
            raise ValueError("not admin")

    async def channels(self, ids):
        return {c: ChannelInfo(self.chans[c]["title"], self.chans[c]["about"], self.chans[c]["photo"] is not None) for c in ids if c in self.chans}

    async def edit_title(self, cid, title):
        self.chans[cid]["title"] = title

    async def edit_about(self, cid, about):
        self.chans[cid]["about"] = about

    async def set_photo(self, cid, path):
        self.chans[cid]["photo"] = Path(path).read_bytes()

    async def delete_photo(self, cid):
        self.chans[cid]["photo"] = None

    async def photo(self, cid):
        return self.chans[cid]["photo"]

    async def send(self, chat_id, path, *, name, caption, media, as_file, thumb, progress):
        cid = chat_id
        if self.flood:
            raise errors.FloodWaitError(request=None, capture=self.flood)
        if self.flood_once:
            seconds, self.flood_once = self.flood_once, None
            raise errors.FloodWaitError(request=None, capture=seconds)
        if self.fail_times:
            self.fail_times -= 1
            raise ConnectionError("network is down")
        self.sending += 1
        self.max_sending = max(self.max_sending, self.sending)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.sending -= 1
        data = Path(path).read_bytes()
        for done in (0, len(data) // 2, len(data)):
            progress(done, len(data))
        mid = self.next_mid
        self.next_mid += 1
        self.chans[cid]["messages"][mid] = {
            "data": data, "caption": caption, "name": name, "as_file": as_file, "media": media,
            "thumb": Path(thumb).read_bytes() if thumb else None, "path": str(path),
        }
        mime = "image/jpeg" if media.kind == "photo" and not as_file else media.mime
        return Sent(mid, len(data), mime, media.width, media.height)

    async def message_media(self, cid, mid):
        m = self.chans[cid]["messages"].get(mid)
        if not m:
            raise TCloudError(404, "file_not_found", "gone")
        return m

    async def iter_media(self, m, offset, length):
        data = m["data"][offset:offset + length]
        for i in range(0, len(data), 7):
            yield data[i:i + 7]

    async def delete(self, cid, ids):
        for i in ids:
            self.chans[cid]["messages"].pop(i, None)

    def messages(self):
        return [m for ch in self.chans.values() for m in ch["messages"].values()]


class FakeScanner:
    def __init__(self):
        self.available = True

    async def ping(self):
        return self.available

    async def scan(self, path):
        if not self.available:
            raise TCloudError(503, "scanner_unavailable", "antivirus is not available, upload rejected")
        if b"EICAR" in Path(path).read_bytes():
            raise TCloudError(422, "infected", "antivirus found Eicar-Signature, file rejected")


@pytest.fixture(scope="session", autouse=True)
def _migrate():
    command.upgrade(Config("alembic.ini"), "head")


def _truncate():
    async def run():
        engine = create_async_engine(DB_URL)
        async with engine.begin() as c:
            await c.execute(text("TRUNCATE tasks, files, folders, storages CASCADE"))
        await engine.dispose()

    asyncio.run(run())


@pytest.fixture
def tg():
    return FakeTelegram()


@pytest.fixture
def scanner():
    return FakeScanner()


@pytest.fixture
def make_client(tmp_path, tg, scanner):
    _truncate()
    opened = []

    def make(**kw) -> TestClient:
        settings = Settings(
            _env_file=None, api_key=KEY, tg_api_id=1, tg_api_hash="x", database_url=DB_URL,
            tmp_dir=tmp_path / "tmp", **{"retry_delay": 0.01, "min_free_disk_mb": 0, **kw},
        )
        c = TestClient(create_app(settings, tg=tg, scanner=scanner), headers={"X-API-Key": KEY})
        c.__enter__()
        opened.append(c)
        return c

    yield make
    for c in opened:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def storage(client) -> dict:
    r = client.post("/storages", json={"name": "Фото", "description": "мои фото"})
    assert r.status_code == 201, r.text
    return r.json()


def wait_task(client, tid, timeout=20) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        t = client.get(f"/tasks/{tid}").json()
        if t["status"] in ("done", "failed"):
            return t
        time.sleep(0.05)
    raise AssertionError(f"task {tid} did not finish: {t}")


def upload(client, path: str, data: bytes, **kw) -> dict:
    r = client.post("/uploads", json={"path": path, "size": len(data), **kw})
    assert r.status_code == 201, r.text
    task = r.json()
    if task["status"] == "done":
        return task
    r = client.put(f"/uploads/{task['id']}", content=data)
    assert r.status_code in (202, 413, 422, 400), r.text
    return wait_task(client, task["id"])


# ---------- образцы медиа ----------

SAMPLES = Path("/tmp/tcloud-samples")


def _ffmpeg(name: str, *args: str) -> bytes:
    SAMPLES.mkdir(exist_ok=True)
    out = SAMPLES / name
    if not out.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(out)], check=True)
    return out.read_bytes()


def video(fmt="mp4", w=320, h=240, extra=()) -> bytes:
    codec = ["-c:v", "libvpx", "-b:v", "200k"] if fmt == "webm" else ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    return _ffmpeg(
        f"v_{w}x{h}_{'_'.join(extra) or 'plain'}.{fmt}".replace("/", ""),
        "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=10", "-f", "lavfi", "-i", "sine=frequency=440",
        "-t", "2", *codec, *extra,
    )


def audio(fmt="mp3") -> bytes:
    return _ffmpeg(f"a.{fmt}", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2",
                   "-metadata", "title=Song", "-metadata", "artist=Band")


def image(fmt="JPEG", size=(64, 48), **save) -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, fmt, **save)
    return buf.getvalue()


def gif_animated() -> bytes:
    import io

    buf = io.BytesIO()
    frames = [Image.new("RGB", (32, 32), c) for c in ((255, 0, 0), (0, 255, 0))]
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return buf.getvalue()
