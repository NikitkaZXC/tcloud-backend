import subprocess
from pathlib import Path

from src.infrastructure.media import analyze


def test_ffmpeg_and_ffprobe_read_only_local_files(monkeypatch, tmp_path):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 1, b"{}", b"")

    monkeypatch.setattr(analyze.subprocess, "run", run)
    analyze._ffprobe(Path("a.mp4"))
    analyze.thumbnail(Path("a.mp4"), tmp_path / "t.jpg", 1.0)
    assert [command[0] for command in commands] == ["ffprobe", "ffmpeg"]
    for command in commands:
        at = command.index("-protocol_whitelist")
        assert command[at + 1] == "file"
    ffmpeg = commands[1]
    assert ffmpeg.index("-protocol_whitelist") < ffmpeg.index("-i")
