import io
import json
import mimetypes
import subprocess
import warnings
from dataclasses import dataclass
from pathlib import Path

import filetype
import pillow_heif
from PIL import Image, ImageOps

from src.infrastructure.enums import MediaKind
from src.infrastructure.limits import (
    PHOTO_MAX,
    PHOTO_RATIO,
    PHOTO_SIDES_SUM,
    THUMB_MAX,
    THUMB_SIDE,
)
from src.infrastructure.media.exception import (
    ExtensionMismatchError,
    InvalidMediaError,
    UnsupportedMediaError,
)

pillow_heif.register_heif_opener()

IMAGE_MIMES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
    "image/gif",
}
AV_MIMES = {
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "video/x-matroska",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/x-flac",
    "audio/x-wav",
}
EXTENSIONS = {
    MediaKind.PHOTO: {"jpg", "jpeg", "png", "webp", "heic", "heif", "gif"},
    MediaKind.ANIMATION: {"gif"},
    MediaKind.VIDEO: {"mp4", "m4v", "mov", "webm", "mkv"},
    MediaKind.AUDIO: {"mp3", "m4a", "ogg", "oga", "opus", "flac", "wav"},
}
ALL_EXTENSIONS = set().union(*EXTENSIONS.values())
DOCUMENT_MIME = "application/octet-stream"
NAME_TYPES = mimetypes.MimeTypes(filenames=())
MEDIA_PREFIXES = ("image/", "audio/", "video/")
PREVIEW_MAX = 20 * 1024
SNIFF_BYTES = 8192
TIMEOUT = 120
UNSUPPORTED = "only photo, video, audio and GIF files are accepted"
DAMAGED = "file is damaged or is not a real media file"


@dataclass
class Media:
    mime: str
    kind: MediaKind
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    title: str | None = None
    performer: str | None = None


def extension(name: str) -> str:
    return name.rpartition(".")[2].lower() if "." in name else ""


def sniff(head: bytes) -> str | None:
    guess = filetype.guess(head)
    return guess.mime if guess else None


def mime_by_name(name: str | None) -> str | None:
    guess = NAME_TYPES.guess_type(f"x.{extension(name)}")[0] if name else None
    return None if not guess or guess.startswith(MEDIA_PREFIXES) else guess


def probe(path: Path, name: str | None) -> Media:
    with open(path, "rb") as file:
        mime = sniff(file.read(SNIFF_BYTES))
    if mime in IMAGE_MIMES:
        media = _image(path, mime)
    elif mime in AV_MIMES:
        media = _audio_video(path, mime)
    else:
        media = Media(mime or mime_by_name(name) or DOCUMENT_MIME, MediaKind.DOCUMENT)
    if name is not None and not _name_fits(media.kind, extension(name)):
        raise ExtensionMismatchError(
            f"file content is {media.kind} ({media.mime}), but the name ends with .{extension(name)}"
        )
    return media


def _name_fits(kind: MediaKind, ext: str) -> bool:
    if kind == MediaKind.DOCUMENT:
        return ext not in ALL_EXTENSIONS
    return ext in EXTENSIONS[kind]


def _image(path: Path, mime: str) -> Media:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
                width, height = image.size
                frames = getattr(image, "n_frames", 1)
    except Image.DecompressionBombError, Image.DecompressionBombWarning:
        raise InvalidMediaError("image has too many pixels") from None
    except Exception:
        raise InvalidMediaError(DAMAGED) from None
    kind = (
        MediaKind.ANIMATION if mime == "image/gif" and frames > 1 else MediaKind.PHOTO
    )
    return Media(mime, kind, width, height)


def _ffprobe(path: Path) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        return json.loads(
            subprocess.run(
                command, capture_output=True, timeout=TIMEOUT, check=True
            ).stdout
        )
    except subprocess.SubprocessError, ValueError:
        raise InvalidMediaError(DAMAGED) from None


def _float(value: object) -> float | None:
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def _rotation(stream: dict) -> int:
    rotation = (stream.get("tags") or {}).get("rotate")
    for side_data in stream.get("side_data_list") or []:
        rotation = side_data.get("rotation", rotation)
    return abs(int(_float(rotation) or 0)) % 360


def _audio_video(path: Path, mime: str) -> Media:
    info = _ffprobe(path)
    streams = info.get("streams") or []
    video = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "video"
            and not (stream.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    audio = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"), None
    )
    if not video and not audio:
        raise InvalidMediaError(DAMAGED)
    container = info.get("format") or {}
    duration = _float(container.get("duration")) or _float(
        (video or audio).get("duration")
    )
    if video:
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        if not width or not height:
            raise InvalidMediaError(DAMAGED)
        if _rotation(video) in (90, 270):
            width, height = height, width
        return Media(
            mime if mime.startswith("video/") else "video/mp4",
            MediaKind.VIDEO,
            width,
            height,
            duration,
        )
    tags = {
        key.lower(): value
        for key, value in {
            **(audio.get("tags") or {}),
            **(container.get("tags") or {}),
        }.items()
    }
    if not mime.startswith("audio/"):
        mime = {"video/webm": "audio/webm", "video/x-matroska": "audio/x-matroska"}.get(
            mime, "audio/mp4"
        )
    return Media(
        mime,
        MediaKind.AUDIO,
        duration=duration,
        title=tags.get("title"),
        performer=tags.get("artist"),
    )


def photo_fits(media: Media, size: int) -> bool:
    width, height = media.width or 0, media.height or 0
    return (
        size <= PHOTO_MAX
        and 0 < width + height <= PHOTO_SIDES_SUM
        and max(width, height) <= PHOTO_RATIO * min(width, height)
    )


def to_jpeg(source: Path, target: Path) -> None:
    with Image.open(source) as image:
        image.convert("RGB").save(target, "JPEG", quality=95)


def thumbnail(source: Path, target: Path, duration: float | None) -> bool:
    scale = f"scale={THUMB_SIDE}:{THUMB_SIDE}:force_original_aspect_ratio=decrease"
    position = str(min(1.0, (duration or 0) / 2))
    for quality in (5, 15, 31):
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            position,
            "-protocol_whitelist",
            "file",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            scale,
            "-q:v",
            str(quality),
            str(target),
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=TIMEOUT)
        except subprocess.SubprocessError:
            return False
        if result.returncode or not target.exists():
            return False
        if target.stat().st_size <= THUMB_MAX:
            return True
    return False


def preview(source: Path) -> bytes:
    with Image.open(source) as image:
        image.thumbnail((THUMB_SIDE, THUMB_SIDE))
        frame = ImageOps.exif_transpose(image).convert("RGB")
    for quality in (80, 60, 40, 20):
        buffer = io.BytesIO()
        frame.save(buffer, "JPEG", quality=quality)
        if buffer.tell() <= PREVIEW_MAX:
            break
    return buffer.getvalue()
