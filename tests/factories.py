import io
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

SAMPLES = Path(tempfile.gettempdir()) / "tcloud-samples"


def _ffmpeg(name: str, *args: str) -> bytes:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    target = SAMPLES / name
    if not target.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(target)], check=True)
    return target.read_bytes()


def make_video(fmt: str = "mp4", width: int = 320, height: int = 240) -> bytes:
    codec = (
        ["-c:v", "libvpx", "-b:v", "200k"]
        if fmt == "webm"
        else ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    )
    return _ffmpeg(
        f"video_{width}x{height}.{fmt}",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={width}x{height}:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440",
        "-t",
        "2",
        *codec,
    )


def make_audio(fmt: str = "mp3") -> bytes:
    return _ffmpeg(
        f"audio.{fmt}",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440",
        "-t",
        "2",
        "-metadata",
        "title=Song",
        "-metadata",
        "artist=Band",
    )


def make_image(fmt: str = "JPEG", size: tuple[int, int] = (64, 48)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buffer, fmt)
    return buffer.getvalue()


def make_gif_animated() -> bytes:
    buffer = io.BytesIO()
    frames = [Image.new("RGB", (32, 32), color) for color in ((255, 0, 0), (0, 255, 0))]
    frames[0].save(
        buffer, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0
    )
    return buffer.getvalue()


EXE = b"MZ" + b"\0" * 5000
