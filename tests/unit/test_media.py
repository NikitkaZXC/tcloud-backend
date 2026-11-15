import subprocess
from pathlib import Path

import pytest
from PIL import Image

from src.infrastructure import limits
from src.infrastructure.enums import MediaKind
from src.infrastructure.media import (
    ExtensionMismatchError,
    InvalidMediaError,
    Media,
    UnsupportedMediaError,
    check_name,
    photo_fits,
    probe,
    thumbnail,
    to_jpeg,
)
from tests.factories import EXE, make_audio, make_gif_animated, make_image, make_video


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
        b"%PDF-1.7 fake pdf",
        EXE,
        b"\x7fELF linux binary",
        b"PK\x03\x04 zip archive",
        b"#!/bin/sh\nrm -rf /",
        b"",
    ],
)
def test_non_media_rejected(tmp_path, data):
    with pytest.raises(UnsupportedMediaError):
        probe(_file(tmp_path, data), "a.jpg")


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
    check_name("a.MP4")
    for bad in ("a.exe", "a.pdf", "noext", "a.jpg.exe"):
        with pytest.raises(UnsupportedMediaError):
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
