"""Определение и проверка медиа (ТЗ §5, §8). Файлы только читаются декодерами, никогда не исполняются."""

from __future__ import annotations

import json
import subprocess
import warnings
from dataclasses import dataclass
from pathlib import Path

import filetype
import pillow_heif
from PIL import Image

from . import limits
from .errors import TCloudError

pillow_heif.register_heif_opener()

IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "image/gif"}
AV_MIMES = {
    "video/mp4", "video/quicktime", "video/webm", "video/x-matroska",
    "audio/mpeg", "audio/mp4", "audio/ogg", "audio/x-flac", "audio/x-wav",
}
EXTS = {
    "photo": {"jpg", "jpeg", "png", "webp", "heic", "heif", "gif"},
    "animation": {"gif"},
    "video": {"mp4", "m4v", "mov", "webm", "mkv"},
    "audio": {"mp3", "m4a", "ogg", "oga", "opus", "flac", "wav"},
}
ALL_EXTS = set().union(*EXTS.values())
SNIFF_BYTES = 8192
TIMEOUT = 120  # с на ffprobe/ffmpeg


@dataclass
class Media:
    mime: str
    kind: str
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    title: str | None = None
    performer: str | None = None


def _unsupported() -> TCloudError:
    return TCloudError(415, "unsupported_media_type", "only photo, video, audio and GIF files are accepted")


def _invalid(message: str = "file is damaged or is not a real media file") -> TCloudError:
    return TCloudError(422, "invalid_media", message)


def ext(name: str) -> str:
    return name.rpartition(".")[2].lower() if "." in name else ""


def check_name(name: str) -> None:
    if ext(name) not in ALL_EXTS:
        raise _unsupported()


def sniff(head: bytes) -> str:
    """MIME по magic bytes; расширение и Content-Type не учитываются."""
    guess = filetype.guess(head)
    if not guess or guess.mime not in IMAGE_MIMES | AV_MIMES:
        raise _unsupported()
    return guess.mime


def probe(path: Path, name: str | None) -> Media:
    with open(path, "rb") as f:
        mime = sniff(f.read(SNIFF_BYTES))
    m = _image(path, mime) if mime in IMAGE_MIMES else _av(path, mime)
    if name is not None and ext(name) not in EXTS[m.kind]:
        raise TCloudError(422, "extension_mismatch", f"file content is {m.kind} ({m.mime}), but the name ends with .{ext(name)}")
    return m


def _image(path: Path, mime: str) -> Media:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:
                im.load()  # полное декодирование первого кадра
                w, h = im.size
                frames = getattr(im, "n_frames", 1)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise _invalid("image has too many pixels") from None
    except Exception:
        raise _invalid() from None
    return Media(mime, "animation" if mime == "image/gif" and frames > 1 else "photo", w, h)


def _ffprobe(path: Path) -> dict:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, timeout=TIMEOUT, check=True,
        ).stdout
        return json.loads(out)
    except (subprocess.SubprocessError, ValueError):
        raise _invalid() from None


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rotation(stream: dict) -> int:
    r = (stream.get("tags") or {}).get("rotate")
    for d in stream.get("side_data_list") or []:
        r = d.get("rotation", r)
    return abs(int(_float(r) or 0)) % 360


def _av(path: Path, mime: str) -> Media:
    info = _ffprobe(path)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video and not audio:
        raise _invalid()
    fmt = info.get("format") or {}
    duration = _float(fmt.get("duration")) or _float((video or audio).get("duration"))
    # Контейнер (mp4/ogg/webm) ещё не говорит, видео это или аудио — решают потоки
    if video:
        w, h = int(video.get("width") or 0), int(video.get("height") or 0)
        if not w or not h:
            raise _invalid()
        if _rotation(video) in (90, 270):
            w, h = h, w
        return Media(mime if mime.startswith("video/") else "video/mp4", "video", w, h, duration)
    # Теги бывают в контейнере (mp3, m4a) или в потоке (ogg)
    tags = {k.lower(): v for k, v in {**(audio.get("tags") or {}), **(fmt.get("tags") or {})}.items()}
    if not mime.startswith("audio/"):
        mime = {"video/webm": "audio/webm", "video/x-matroska": "audio/x-matroska"}.get(mime, "audio/mp4")
    return Media(mime, "audio", duration=duration, title=tags.get("title"), performer=tags.get("artist"))


def photo_fits(m: Media, size: int) -> bool:
    """Влезет ли фото в лимиты Telegram для сжатого фото."""
    w, h = m.width or 0, m.height or 0
    return size <= limits.PHOTO_MAX and 0 < w + h <= limits.PHOTO_SIDES_SUM and max(w, h) <= limits.PHOTO_RATIO * min(w, h)


def to_jpeg(src: Path, dst: Path) -> None:
    """Telegram принимает как фото только JPEG/PNG — webp/heic/gif переводим в JPEG (TG всё равно сожмёт)."""
    with Image.open(src) as im:
        im.convert("RGB").save(dst, "JPEG", quality=95)


def thumbnail(src: Path, dst: Path, duration: float | None) -> bool:
    """Превью видео в лимитах Telegram: JPEG ≤ 200 КБ, сторона ≤ 320 px."""
    side = limits.THUMB_SIDE
    vf = f"scale={side}:{side}:force_original_aspect_ratio=decrease"
    at = str(min(1.0, (duration or 0) / 2))
    for q in (5, 15, 31):
        try:
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-ss", at, "-i", str(src), "-frames:v", "1", "-vf", vf, "-q:v", str(q), str(dst)],
                capture_output=True, timeout=TIMEOUT,
            )
        except subprocess.SubprocessError:
            return False
        if r.returncode or not dst.exists():
            return False
        if dst.stat().st_size <= limits.THUMB_MAX:
            return True
    return False
