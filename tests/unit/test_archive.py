import asyncio
import io
import tarfile
import zipfile
from datetime import UTC, datetime
from types import SimpleNamespace

import py7zr
import pytest

from src.application.services.archive import build_archive, entry_name
from src.config import settings
from src.infrastructure.archive import ArchiveWriter
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


def archived_files() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            id=index,
            folder_path="a",
            name=f"{index}.bin",
            size=3_000_000,
            tg_message_id=index,
            encryption_header=None,
            updated_at=datetime.now(UTC),
        )
        for index in range(2)
    ]


async def nothing() -> None:
    return None


async def test_build_archive_reports_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.upload, "TMP_DIR", tmp_path)
    target = tmp_path / "out.7z"
    seen: list[int] = []
    warnings = await build_archive(
        Download(),
        1,
        archived_files(),
        {},
        "",
        ArchiveFormat.SEVEN_Z,
        target,
        seen.append,
        nothing,
    )
    assert warnings == [] and seen[-1] == 6_000_000 and seen == sorted(seen)
    with py7zr.SevenZipFile(target) as archive:
        archive.extractall(tmp_path / "out")
    assert (tmp_path / "out" / "a" / "1.bin").read_bytes() == b"x" * 3_000_000
    assert target.stat().st_size < 100_000
    assert sorted(path.name for path in tmp_path.iterdir()) == ["out", "out.7z"]


async def test_cancelled_build_leaves_no_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.upload, "TMP_DIR", tmp_path)
    calls = 0

    async def checkpoint() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await build_archive(
            Download(),
            1,
            archived_files(),
            {},
            "",
            ArchiveFormat.ZIP,
            tmp_path / "out.zip",
            lambda done: None,
            checkpoint,
        )
    assert [path.name for path in tmp_path.iterdir()] == ["out.zip"]


@pytest.mark.parametrize("archive_format", list(ArchiveFormat))
async def test_failed_build_closes_the_writer(archive_format, tmp_path, monkeypatch):
    monkeypatch.setattr(settings.upload, "TMP_DIR", tmp_path)
    aborted: list[ArchiveWriter] = []
    original = ArchiveWriter.abort

    def abort(self) -> None:
        original(self)
        aborted.append(self)

    monkeypatch.setattr(ArchiveWriter, "abort", abort)
    calls = 0

    async def checkpoint() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("telegram died")

    with pytest.raises(RuntimeError):
        await build_archive(
            Download(),
            1,
            archived_files(),
            {},
            "",
            archive_format,
            tmp_path / "out",
            lambda done: None,
            checkpoint,
        )
    [writer] = aborted
    if writer._zip:
        assert writer._zip.fp is None
    if writer._tar:
        assert writer._tar.closed


def test_compression_level_is_six():
    from src.infrastructure import archive

    assert archive.LEVEL == 6
    assert archive.SEVEN_Z_FILTERS[0]["preset"] == 6
    assert {
        options.get("compresslevel", options.get("preset"))
        for _, options in archive.TAR_MODES.values()
    } == {6}
