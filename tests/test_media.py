from pathlib import Path

import pytest
from PIL import Image

from tcloud import limits, media
from tcloud.errors import TCloudError
from tests.conftest import audio, gif_animated, image, video


def _file(tmp_path: Path, data: bytes, name="f") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


@pytest.mark.parametrize("fmt,name,mime", [
    ("JPEG", "a.jpg", "image/jpeg"),
    ("PNG", "a.png", "image/png"),
    ("WEBP", "a.webp", "image/webp"),
    ("GIF", "a.gif", "image/gif"),
])
def test_images(tmp_path, fmt, name, mime):
    m = media.probe(_file(tmp_path, image(fmt)), name)
    assert (m.mime, m.kind, m.width, m.height) == (mime, "photo", 64, 48)


def test_heic(tmp_path):
    m = media.probe(_file(tmp_path, image("HEIF")), "a.heic")
    assert m.kind == "photo" and m.mime in ("image/heic", "image/heif")


def test_animated_gif(tmp_path):
    m = media.probe(_file(tmp_path, gif_animated()), "a.gif")
    assert m.kind == "animation"


@pytest.mark.parametrize("fmt", ["mp4", "mov", "mkv", "webm"])
def test_videos(tmp_path, fmt):
    m = media.probe(_file(tmp_path, video(fmt)), f"a.{fmt}")
    assert m.kind == "video" and (m.width, m.height) == (320, 240)
    assert 1.5 < m.duration < 2.5


def test_rotated_video_swaps_sides(tmp_path):
    src = _file(tmp_path, video("mp4"), "src.mp4")
    import subprocess

    out = tmp_path / "rot.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-display_rotation", "90", "-i", str(src), "-c", "copy", str(out)], check=True)
    m = media.probe(out, "rot.mp4")
    assert (m.width, m.height) == (240, 320)


@pytest.mark.parametrize("fmt,mime", [("mp3", "audio/mpeg"), ("wav", "audio/x-wav"), ("flac", "audio/x-flac"), ("ogg", "audio/ogg"), ("m4a", "audio/mp4")])
def test_audio(tmp_path, fmt, mime):
    m = media.probe(_file(tmp_path, audio(fmt)), f"a.{fmt}")
    assert (m.kind, m.mime) == ("audio", mime)
    assert m.duration and m.duration > 1.5
    assert m.title == "Song" and m.performer == "Band"


@pytest.mark.parametrize("data", [
    b"%PDF-1.7 fake pdf",
    b"MZ\x90\x00 windows exe",
    b"\x7fELF linux binary",
    b"PK\x03\x04 zip archive",
    b"#!/bin/sh\nrm -rf /",
    b"",
])
def test_non_media_rejected(tmp_path, data):
    with pytest.raises(TCloudError) as e:
        media.probe(_file(tmp_path, data), "a.jpg")
    assert e.value.status == 415


def test_exe_renamed_to_jpg_rejected(tmp_path):
    with pytest.raises(TCloudError) as e:
        media.probe(_file(tmp_path, b"MZ" + b"\0" * 1000), "virus.jpg")
    assert e.value.code == "unsupported_media_type"


def test_extension_mismatch(tmp_path):
    with pytest.raises(TCloudError) as e:
        media.probe(_file(tmp_path, image("JPEG")), "clip.mp4")
    assert e.value.code == "extension_mismatch"


def test_truncated_image(tmp_path):
    data = image("PNG", (300, 300))
    with pytest.raises(TCloudError) as e:
        media.probe(_file(tmp_path, data[: len(data) // 2]), "a.png")
    assert e.value.code == "invalid_media"


def test_damaged_video(tmp_path):
    data = video("mp4")
    with pytest.raises(TCloudError) as e:
        media.probe(_file(tmp_path, data[:200]), "a.mp4")
    assert e.value.code == "invalid_media"


def test_decompression_bomb(tmp_path, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    with pytest.raises(TCloudError) as e:
        media.probe(_file(tmp_path, image("PNG", (100, 100))), "a.png")
    assert e.value.code == "invalid_media"


def test_check_name():
    media.check_name("a.MP4")
    for bad in ("a.exe", "a.pdf", "noext", "a.jpg.exe"):
        with pytest.raises(TCloudError):
            media.check_name(bad)


@pytest.mark.parametrize("w,h,size,ok", [
    (4000, 3000, 1000, True),
    (4000, 3000, limits.PHOTO_MAX + 1, False),
    (6000, 4001, 1000, False),  # сумма сторон > 10000
    (2100, 100, 1000, False),  # соотношение > 20
    (2000, 100, 1000, True),
])
def test_photo_fits(w, h, size, ok):
    assert media.photo_fits(media.Media("image/jpeg", "photo", w, h), size) is ok


def test_thumbnail_limits(tmp_path):
    src = _file(tmp_path, video("mp4", 1280, 720), "v.mp4")
    thumb = tmp_path / "t.jpg"
    assert media.thumbnail(src, thumb, 2.0)
    assert thumb.stat().st_size <= limits.THUMB_MAX
    with Image.open(thumb) as im:
        assert max(im.size) <= limits.THUMB_SIDE and im.size == (320, 180)


def test_to_jpeg(tmp_path):
    dst = tmp_path / "o.jpg"
    media.to_jpeg(_file(tmp_path, image("WEBP")), dst)
    assert media.probe(dst, "o.jpg").mime == "image/jpeg"
