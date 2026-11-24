import pytest

from src.application.services.temp import temp_path


@pytest.mark.parametrize(
    "name,suffix",
    [
        ("photo.JPG", ".jpg"),
        ("clip.mp4", ".mp4"),
        ("a." + "x" * 250, ""),
        ("a." + "x" * 16, "." + "x" * 16),
        ("tcloud/s/dir.x/file", ""),
        ("файл.тхт", ""),
        ("noext", ""),
        ("a.b c", ""),
    ],
)
def test_temp_path_keeps_only_short_extensions(name, suffix):
    path = temp_path(name)
    assert path.suffix == suffix and len(path.name) == 32 + len(suffix)
