import asyncio

import pytest
from telethon import functions, types

from tcloud import telegram


class FakeClient:
    def __init__(self, delay=0.005, fail_part=None):
        self.parts: dict[int, bytes] = {}
        self.delay, self.fail_part = delay, fail_part
        self.active = self.max_active = 0
        self.requests = []

    async def __call__(self, req):
        self.requests.append(type(req))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            if req.file_part == self.fail_part:
                raise ConnectionError("boom")
            self.parts[req.file_part] = req.bytes
            return True
        finally:
            self.active -= 1


def _tg(client, workers=4):
    tg = telegram.Telegram.__new__(telegram.Telegram)
    tg.client, tg.upload_workers = client, workers
    return tg


def test_big_file_parts_uploaded_in_parallel_once(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram, "BIG_FILE", 1024 * 1024)
    data = bytes(range(256)) * (13 * 1024)  # 3.25 МиБ → 7 кусков, последний неполный
    path = tmp_path / "v.mp4"
    path.write_bytes(data)
    client = FakeClient()
    progress = []
    handle = asyncio.run(_tg(client).upload(path, lambda d, t: progress.append((d, t))))
    assert isinstance(handle, types.InputFileBig) and handle.parts == 7 and handle.name == "v.mp4"
    assert b"".join(client.parts[i] for i in range(7)) == data
    assert set(client.requests) == {functions.upload.SaveBigFilePartRequest}
    assert client.max_active == 4
    assert progress[-1] == (len(data), len(data))


def test_small_file_single_worker(tmp_path):
    path = tmp_path / "a.jpg"
    path.write_bytes(b"x" * (telegram.CHUNK * 2 + 10))
    client = FakeClient()
    handle = asyncio.run(_tg(client).upload(path, lambda *a: None))
    assert isinstance(handle, types.InputFile) and handle.parts == 3
    assert client.max_active == 1
    assert set(client.requests) == {functions.upload.SaveFilePartRequest}


def test_empty_file_is_one_part(tmp_path):
    path = tmp_path / "a.jpg"
    path.write_bytes(b"")
    handle = asyncio.run(_tg(FakeClient()).upload(path, lambda *a: None))
    assert handle.parts == 1


def test_failed_part_stops_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram, "BIG_FILE", 1024)
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x" * (telegram.CHUNK * 10))
    client = FakeClient(fail_part=2)
    with pytest.raises(ConnectionError):
        asyncio.run(_tg(client).upload(path, lambda *a: None))
    assert len(client.parts) < 10  # остальные воркеры остановлены


def test_transient_errors_list():
    assert issubclass(ConnectionError, telegram.TRANSIENT)
    assert not isinstance(ValueError(), telegram.TRANSIENT)
