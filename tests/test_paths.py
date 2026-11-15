import uuid

import pytest

from tcloud import paths
from tcloud.errors import TCloudError

SID = uuid.uuid4()


def test_split_file():
    assert paths.split_file(f"tcloud/{SID}/отпуск/2026/photo.jpg") == (SID, "отпуск/2026", "photo.jpg")


def test_split_file_in_root():
    assert paths.split_file(f"tcloud/{SID}/photo.jpg") == (SID, "", "photo.jpg")


def test_split_folder_root():
    assert paths.split_folder(f"tcloud/{SID}") == (SID, "")


def test_join_roundtrip():
    raw = f"tcloud/{SID}/a/b/c.mp4"
    assert paths.join(*paths.split_file(raw)) == raw
    assert paths.join(SID, "") == f"tcloud/{SID}"


def test_nfc_normalization():
    decomposed = f"tcloud/{SID}/é.jpg"  # e + combining acute
    assert paths.split_file(decomposed)[2] == "é.jpg"


@pytest.mark.parametrize("raw", [
    "photo.jpg",
    f"cloud/{SID}/a.jpg",
    "tcloud/not-a-uuid/a.jpg",
    f"tcloud/{SID.hex}/a.jpg",  # не канонический вид
    f"tcloud/{str(SID).upper()}x/a.jpg",
    f"tcloud/{SID}/a//b.jpg",
    f"tcloud/{SID}/a/",
    f"tcloud/{SID}/../x.jpg",
    f"tcloud/{SID}/./x.jpg",
    f"tcloud/{SID}/a\\b.jpg",
    f"tcloud/{SID}/a\x00.jpg",
    f"tcloud/{SID}/a\n.jpg",
    f"tcloud/{SID}/" + "я" * 128 + ".jpg",  # > 255 байт
    f"tcloud/{SID}/" + "a/" * 600 + "x.jpg",  # > 1024 символов
])
def test_invalid_paths(raw):
    with pytest.raises(TCloudError) as e:
        paths.split_file(raw)
    assert e.value.code == "invalid_path"


def test_file_name_required():
    with pytest.raises(TCloudError):
        paths.split_file(f"tcloud/{SID}")


def test_uppercase_uuid_is_accepted():
    assert paths.split_folder(f"tcloud/{str(SID).upper()}")[0] == SID
