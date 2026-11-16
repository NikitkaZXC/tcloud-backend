import subprocess
from pathlib import Path

import pytest
from PIL import Image

from src.infrastructure import limits
from src.infrastructure.enums import MediaKind
from src.infrastructure.media import (
    BlockedFileError,
    ExtensionMismatchError,
    InvalidMediaError,
    Media,
    check_name,
    photo_fits,
    probe,
    thumbnail,
    to_jpeg,
)
from tests.factories import (
    EXE,
    PDF,
    make_audio,
    make_gif_animated,
    make_image,
    make_video,
    make_zip,
)


def _file(tmp_path: Path, data: bytes, name: str = "f") -> Path:
    target = tmp_path / name
    target.write_bytes(data)
    return target


@pytest.mark.parametrize(
    "fmt,name,mime",
    [
        ("JPEG", "a.jpg", "image/jpeg"),
        ("PNG", "a.png", "image/png"),
        ("WEBP", "a.webp", "image/webp"),
        ("GIF", "a.gif", "image/gif"),
    ],
)
def test_images(tmp_path, fmt, name, mime):
    media = probe(_file(tmp_path, make_image(fmt)), name)
    assert (media.mime, media.kind, media.width, media.height) == (
        mime,
        MediaKind.PHOTO,
        64,
        48,
    )


def test_heic(tmp_path):
    media = probe(_file(tmp_path, make_image("HEIF")), "a.heic")
    assert media.kind == MediaKind.PHOTO and media.mime in ("image/heic", "image/heif")


def test_animated_gif(tmp_path):
    assert (
        probe(_file(tmp_path, make_gif_animated()), "a.gif").kind == MediaKind.ANIMATION
    )


@pytest.mark.ffmpeg
@pytest.mark.parametrize("fmt", ["mp4", "mov", "mkv", "webm"])
def test_videos(tmp_path, fmt):
    media = probe(_file(tmp_path, make_video(fmt)), f"a.{fmt}")
    assert media.kind == MediaKind.VIDEO and (media.width, media.height) == (320, 240)
    assert 1.5 < media.duration < 2.5


@pytest.mark.ffmpeg
def test_rotated_video_swaps_sides(tmp_path):
    source = _file(tmp_path, make_video("mp4"), "source.mp4")
    rotated = tmp_path / "rotated.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-display_rotation",
            "90",
            "-i",
            str(source),
            "-c",
            "copy",
            str(rotated),
        ],
        check=True,
    )
    media = probe(rotated, "rotated.mp4")
    assert (media.width, media.height) == (240, 320)


@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    "fmt,mime",
    [
        ("mp3", "audio/mpeg"),
        ("wav", "audio/x-wav"),
        ("flac", "audio/x-flac"),
        ("ogg", "audio/ogg"),
        ("m4a", "audio/mp4"),
    ],
)
def test_audio(tmp_path, fmt, mime):
    media = probe(_file(tmp_path, make_audio(fmt)), f"a.{fmt}")
    assert (media.kind, media.mime) == (MediaKind.AUDIO, mime)
    assert media.duration and media.duration > 1.5
    assert media.title == "Song" and media.performer == "Band"


@pytest.mark.parametrize(
    "data",
    [
        EXE,
        b"\x7fELF linux binary",
        b"#!/bin/sh\nrm -rf /",
        b"\xcf\xfa\xed\xfe mach-o",
        b"\xca\xfe\xba\xbe java class",
        b"dex\n035 android",
    ],
)
@pytest.mark.parametrize("name", ["a.jpg", "report.pdf", "noext"])
def test_executable_content_blocked(tmp_path, data, name):
    with pytest.raises(BlockedFileError):
        probe(_file(tmp_path, data), name)


@pytest.mark.parametrize(
    "data,name,mime",
    [
        (PDF, "report.pdf", "application/pdf"),
        (b"just text", "notes.txt", "application/octet-stream"),
        (b"", "empty", "application/octet-stream"),
        (make_zip({"a.txt": b"x", "dir/b.csv": b"y"}), "a.zip", "application/zip"),
    ],
)
def test_documents(tmp_path, data, name, mime):
    media = probe(_file(tmp_path, data), name)
    assert (media.kind, media.mime, media.width) == (MediaKind.DOCUMENT, mime, None)


@pytest.mark.parametrize("data", [PDF, b"", make_zip({"a.txt": b"x"})])
def test_document_named_as_media_mismatch(tmp_path, data):
    with pytest.raises(ExtensionMismatchError):
        probe(_file(tmp_path, data), "a.jpg")


def test_media_named_as_document_mismatch(tmp_path):
    with pytest.raises(ExtensionMismatchError):
        probe(_file(tmp_path, make_image("JPEG")), "a.txt")


@pytest.mark.parametrize(
    "member",
    ["setup.exe", "dir/run.BAT", "Evil.app/Contents/MacOS/Evil", "lib\\x.dll"],
)
def test_archive_with_executable_blocked(tmp_path, member):
    data = make_zip({"readme.txt": b"x", member: b"payload"})
    with pytest.raises(BlockedFileError, match="archive contains"):
        probe(_file(tmp_path, data), "a.zip")


def test_office_document_is_checked_as_archive(tmp_path):
    data = make_zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w/>"})
    assert probe(_file(tmp_path, data), "a.docx").kind == MediaKind.DOCUMENT


def test_extension_mismatch(tmp_path):
    with pytest.raises(ExtensionMismatchError):
        probe(_file(tmp_path, make_image("JPEG")), "clip.mp4")


def test_truncated_image(tmp_path):
    data = make_image("PNG", (300, 300))
    with pytest.raises(InvalidMediaError):
        probe(_file(tmp_path, data[: len(data) // 2]), "a.png")


@pytest.mark.ffmpeg
def test_damaged_video(tmp_path):
    with pytest.raises(InvalidMediaError):
        probe(_file(tmp_path, make_video("mp4")[:200]), "a.mp4")


def test_decompression_bomb(tmp_path, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    with pytest.raises(InvalidMediaError):
        probe(_file(tmp_path, make_image("PNG", (100, 100))), "a.png")


def test_check_name():
    for good in ("a.MP4", "a.pdf", "noext", "archive.tar.gz", "a.exe.txt"):
        check_name(good)
    for bad in ("a.exe", "a.jpg.exe", "a.EXE", "a.exe.", "a.exe ", "run.sh", "x.apk"):
        with pytest.raises(BlockedFileError):
            check_name(bad)


@pytest.mark.parametrize(
    "width,height,size,ok",
    [
        (4000, 3000, 1000, True),
        (4000, 3000, limits.PHOTO_MAX + 1, False),
        (6000, 4001, 1000, False),
        (2100, 100, 1000, False),
        (2000, 100, 1000, True),
    ],
)
def test_photo_fits(width, height, size, ok):
    assert photo_fits(Media("image/jpeg", MediaKind.PHOTO, width, height), size) is ok


@pytest.mark.ffmpeg
def test_thumbnail_limits(tmp_path):
    source = _file(tmp_path, make_video("mp4", 1280, 720), "v.mp4")
    target = tmp_path / "thumb.jpg"
    assert thumbnail(source, target, 2.0)
    assert target.stat().st_size <= limits.THUMB_MAX
    with Image.open(target) as image:
        assert image.size == (320, 180)


def test_to_jpeg(tmp_path):
    target = tmp_path / "o.jpg"
    to_jpeg(_file(tmp_path, make_image("WEBP")), target)
    assert probe(target, "o.jpg").mime == "image/jpeg"
