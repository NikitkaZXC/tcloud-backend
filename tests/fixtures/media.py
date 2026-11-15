import shutil
import subprocess
from pathlib import Path

import pytest

from tests.factories import SAMPLES

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    skip = pytest.mark.skip(reason="ffmpeg/ffprobe not found: run make test-docker")
    for item in items:
        for marker, directory in (
            ("unit", "tests/unit/"),
            ("integration", "tests/integration/"),
            ("api", "tests/api/"),
        ):
            if directory in item.nodeid:
                item.add_marker(getattr(pytest.mark, marker))
        if "ffmpeg" in item.keywords and not HAS_FFMPEG:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def _samples_dir() -> Path:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    return SAMPLES


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)
