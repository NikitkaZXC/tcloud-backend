import uuid

import pytest

from src.utilities.parsers import InvalidPathError, join_path, split_file, split_folder

SID = uuid.uuid4()


def test_split_file():
    assert split_file(f"tcloud/{SID}/отпуск/2026/photo.jpg") == (
        SID,
        "отпуск/2026",
        "photo.jpg",
    )


def test_split_file_in_root():
    assert split_file(f"tcloud/{SID}/photo.jpg") == (SID, "", "photo.jpg")


def test_split_folder_root():
    assert split_folder(f"tcloud/{SID}") == (SID, "")


def test_join_roundtrip():
    raw = f"tcloud/{SID}/a/b/c.mp4"
    assert join_path(*split_file(raw)) == raw
    assert join_path(SID, "") == f"tcloud/{SID}"


def test_nfc_normalization():
    decomposed = f"tcloud/{SID}/é.jpg"
    assert split_file(decomposed)[2] == "é.jpg"


@pytest.mark.parametrize(
    "raw",
    [
        "photo.jpg",
        f"cloud/{SID}/a.jpg",
        "tcloud/not-a-uuid/a.jpg",
        f"tcloud/{SID.hex}/a.jpg",
        f"tcloud/{str(SID).upper()}x/a.jpg",
        f"tcloud/{SID}/a//b.jpg",
        f"tcloud/{SID}/a/",
        f"tcloud/{SID}/../x.jpg",
        f"tcloud/{SID}/./x.jpg",
        f"tcloud/{SID}/a\\b.jpg",
        f"tcloud/{SID}/a\x00.jpg",
        f"tcloud/{SID}/a\n.jpg",
        f"tcloud/{SID}/invoice\u202egpj.exe",
        f"tcloud/{SID}/a\u200b.jpg",
        f"tcloud/{SID}/\ufeffa.jpg",
        f"tcloud/{SID}/" + "я" * 128 + ".jpg",
        f"tcloud/{SID}/" + "a/" * 600 + "x.jpg",
    ],
)
def test_invalid_paths(raw):
    with pytest.raises(InvalidPathError) as e:
        split_file(raw)
    assert e.value.code == "invalid_path"


def test_file_name_required():
    with pytest.raises(InvalidPathError):
        split_file(f"tcloud/{SID}")


def test_uppercase_uuid_is_accepted():
    assert split_folder(f"tcloud/{str(SID).upper()}")[0] == SID
