import asyncio
import os
import random
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tcloud import crypto
from tcloud.api import create_app
from tcloud.config import Settings
from tcloud.db import Database
from tcloud.service import TCloudService

KEY = os.urandom(32)
SEG = 1024
PART = 3 * SEG  # маленькие части, чтобы проверить нарезку без гигабайтов


class FakeBackend:
    """Вместо Telegram — словарь в памяти."""

    def __init__(self):
        self.messages: dict[int, bytes] = {}
        self.next_id = 1

    async def start(self): ...
    async def stop(self): ...

    async def upload(self, path, file_name, caption, progress=None):
        mid = self.next_id
        self.next_id += 1
        self.messages[mid] = Path(path).read_bytes()
        if progress:
            await progress(len(self.messages[mid]), len(self.messages[mid]))
        return -100, mid

    async def stream(self, chat_id, message_id):
        data = self.messages[message_id]
        step = 1000  # кусками, не совпадающими с размером сегмента
        for i in range(0, len(data), step):
            yield data[i:i + step]

    async def delete(self, chat_id, ids):
        for i in ids:
            self.messages.pop(i, None)


def encrypt(data: bytes, part=0, file_id=b"f" * 16, salt=b"s" * 16) -> bytes:
    enc = crypto.PartEncryptor(KEY, file_id, salt, part, SEG)
    out = enc.header
    segs = [data[i:i + SEG] for i in range(0, len(data), SEG)] or [b""]
    for i, s in enumerate(segs):
        out += enc.encrypt_segment(s, last=i == len(segs) - 1)
    return out


def decrypt(blob: bytes, step=777, **expected) -> bytes:
    dec = crypto.PartDecryptor(KEY, **expected)
    out = b""
    for i in range(0, len(blob), step):
        out += b"".join(dec.feed(blob[i:i + step]))
    return out + b"".join(dec.finish())


@pytest.mark.parametrize("size", [0, 1, SEG - 1, SEG, SEG + 1, 5 * SEG, 5 * SEG + 17])
def test_crypto_roundtrip(size):
    data = os.urandom(size)
    blob = encrypt(data)
    assert len(blob) == crypto.encrypted_part_size(size, SEG)
    assert decrypt(blob) == data


def test_crypto_detects_tampering():
    blob = bytearray(encrypt(os.urandom(3 * SEG)))
    blob[100] ^= 1
    with pytest.raises(crypto.CryptoError):
        decrypt(bytes(blob))


def test_crypto_detects_truncation():
    blob = encrypt(os.urandom(3 * SEG))
    # Отрезаем последний сегмент ровно по границе: без флага last это бы прошло незаметно
    with pytest.raises(crypto.CryptoError):
        decrypt(blob[: crypto.HEADER.size + 2 * (SEG + crypto.TAG_SIZE)])


def test_crypto_rejects_wrong_part_or_file():
    blob = encrypt(b"hello", part=1)
    with pytest.raises(crypto.CryptoError):
        decrypt(blob, expected_part_index=0)
    with pytest.raises(crypto.CryptoError):
        decrypt(blob, expected_file_id=b"x" * 16)
    wrong = crypto.PartDecryptor(os.urandom(32))
    wrong.feed(blob)
    with pytest.raises(crypto.CryptoError):
        wrong.finish()


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        api_key="secret", master_key=crypto.generate_master_key(), tg_api_id=1, tg_api_hash="x",
        tg_bot_token="1:x", tg_chat_id="-100", data_dir=tmp_path,
    )
    backend = FakeBackend()
    service = TCloudService(Database(settings.db_path), backend, crypto.load_master_key(settings.master_key),
                            PART, SEG, settings.tmp_dir)
    app = create_app(settings, service)
    with TestClient(app, headers={"X-API-Key": "secret"}) as c:
        c.backend = backend
        yield c


@pytest.mark.parametrize("size", [0, 10, PART, PART + 1, 10 * PART + 333])
def test_upload_split_and_reassemble(client, size):
    data = random.randbytes(size)
    r = client.post("/files?wait=true", files={"file": ("видео.mp4", data, "video/mp4")})
    assert r.status_code == 202, r.text
    info = r.json()
    assert info["status"] == "ready", info
    assert info["parts"] == max(1, -(-size // PART))
    assert len(client.backend.messages) == info["parts"]
    # В Telegram лежит не открытый текст
    if size >= 10:
        assert all(data[:10] not in blob for blob in client.backend.messages.values())

    r = client.get(f"/files/{info['id']}/download")
    assert r.status_code == 200
    assert r.content == data
    assert "filename*=UTF-8''%D0%B2" in r.headers["content-disposition"]

    assert client.delete(f"/files/{info['id']}").status_code == 204
    assert client.backend.messages == {}
    assert client.get(f"/files/{info['id']}").status_code == 404


def test_requires_api_key(client):
    assert client.get("/files", headers={"X-API-Key": "nope"}).status_code == 401


def test_list(client):
    client.post("/files?wait=true", files={"file": ("a.txt", b"a")})
    client.post("/files?wait=true", files={"file": ("b.txt", b"b")})
    assert sorted(f["name"] for f in client.get("/files").json()) == ["a.txt", "b.txt"]
