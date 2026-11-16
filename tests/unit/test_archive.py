import asyncio
import io
import tarfile
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace

import py7zr
import pytest

from src.application.services.archive import entry_name, stream_archive
from src.config import settings
from src.infrastructure.archive import ArchiveWriter
from src.infrastructure.background.disk import DiskBudget
from src.infrastructure.enums import ArchiveFormat

MTIME = 1_800_000_000


def write(archive_format, files, tmp_path):
    output = io.BytesIO()
    writer = ArchiveWriter(archive_format, output)
    for name, data in files.items():
        source = tmp_path / "part"
        source.write_bytes(data)
        writer.add(source, name, MTIME)
    writer.close()
    return output.getvalue()


@pytest.mark.parametrize("archive_format", list(ArchiveFormat))
def test_archive_round_trip(archive_format, tmp_path):
    files = {"отпуск/a.txt": b"a" * 100_000, "b.bin": bytes(range(256)) * 10}
    content = write(archive_format, files, tmp_path)
    assert len(content) < 50_000
    if archive_format == ArchiveFormat.ZIP:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            assert {name: archive.read(name) for name in archive.namelist()} == files
            info = archive.getinfo("b.bin")
            assert info.compress_type == zipfile.ZIP_DEFLATED
            assert info.external_attr >> 16 == 0o644
    elif archive_format == ArchiveFormat.SEVEN_Z:
        with py7zr.SevenZipFile(io.BytesIO(content)) as archive:
            infos = {info.filename: info for info in archive.list()}
            assert set(infos) == set(files)
            assert all(
                info.creationtime.timestamp() == MTIME for info in infos.values()
            )
        target = tmp_path / "out"
        with py7zr.SevenZipFile(io.BytesIO(content)) as archive:
            archive.extractall(target)
        assert {name: (target / name).read_bytes() for name in files} == files
    else:
        with tarfile.open(fileobj=io.BytesIO(content)) as archive:
            members = archive.getmembers()
            assert {
                member.name: archive.extractfile(member).read() for member in members
            } == files
            assert all(
                member.mode == 0o644 and member.mtime == MTIME for member in members
            )


def test_entry_name_is_relative_to_the_archived_folder():
    file = SimpleNamespace(folder_path="a/b/c", name="x.jpg")
    assert entry_name("a", file) == "b/c/x.jpg"
    assert entry_name("a/b/c", file) == "x.jpg"
    assert entry_name("", file) == "a/b/c/x.jpg"


class Download:
    async def message_media(self, chat_id, message_id):
        return b"x" * 3_000_000, 3_000_000

    async def iter_media(self, media, offset, length):
        yield media[offset : offset + length]


async def test_aborted_archive_leaves_no_temporary_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.upload, "TMP_DIR", tmp_path)
    files = [
        SimpleNamespace(
            id=index,
            folder_path="a",
            name=f"{index}.bin",
            size=3_000_000,
            tg_message_id=index,
            updated_at=datetime.now(UTC),
        )
        for index in range(2)
    ]
    disk = DiskBudget(tmp_path, 0)
    body = stream_archive(Download(), 1, files, "", ArchiveFormat.ZIP, disk, 9_000_000)
    assert await anext(body)
    await body.aclose()
    await asyncio.sleep(0.2)
    assert list(tmp_path.iterdir()) == [] and disk.items == set()


async def test_seven_z_is_sent_only_when_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.upload, "TMP_DIR", tmp_path)
    files = [
        SimpleNamespace(
            id=index,
            folder_path="a",
            name=f"{index}.bin",
            size=3_000_000,
            tg_message_id=index,
            updated_at=datetime.now(UTC),
        )
        for index in range(2)
    ]
    disk = DiskBudget(tmp_path, 0)
    body = stream_archive(
        Download(), 1, files, "", ArchiveFormat.SEVEN_Z, disk, 9_000_000
    )
    content = b"".join([chunk async for chunk in body])
    target = tmp_path / "out"
    with py7zr.SevenZipFile(io.BytesIO(content)) as archive:
        archive.extractall(target)
    assert (target / "a" / "1.bin").read_bytes() == b"x" * 3_000_000
    assert len(content) < 100_000
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out"]
