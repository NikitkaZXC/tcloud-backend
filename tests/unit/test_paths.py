import uuid

import pytest

from src.utilities.parsers import (
    InvalidPathError,
    check_segment,
    join_path,
    split_file,
    split_folder,
)

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
        f"tcloud/{SID}/" + "я" * 128 + ".jpg",
        f"tcloud/{SID}/" + "a/" * 600 + "x.jpg",
    ],
)
def test_invalid_paths(raw):
    with pytest.raises(InvalidPathError) as e:
        split_file(raw)
    assert e.value.code == "invalid_path"


BIDI = ["invoice\u202egpj.exe"] + [
    f"a{char}.jpg" for char in "\u202a\u202d\u2066\u2069\u200e\u200f\u061c"
]


@pytest.mark.parametrize("name", BIDI)
def test_bidi_names_rejected_when_new(name):
    for check in (
        lambda: split_file(f"tcloud/{SID}/{name}", new=True),
        lambda: split_folder(f"tcloud/{SID}/{name}/b", new=True),
        lambda: check_segment(name, new=True),
    ):
        with pytest.raises(InvalidPathError) as e:
            check()
        assert e.value.code == "invalid_path"


@pytest.mark.parametrize("name", BIDI)
def test_bidi_names_stay_reachable(name):
    assert split_file(f"tcloud/{SID}/{name}/{name}") == (SID, name, name)
    assert split_folder(f"tcloud/{SID}/{name}") == (SID, name)


@pytest.mark.parametrize(
    "name", ["\U0001f468\u200d\U0001f469\u200d\U0001f467.jpg", "co\u00adop.jpg"]
)
def test_format_characters_outside_bidi_are_accepted(name):
    assert split_file(f"tcloud/{SID}/{name}")[2] == name


def test_file_name_required():
    with pytest.raises(InvalidPathError):
        split_file(f"tcloud/{SID}")


def test_uppercase_uuid_is_accepted():
    assert split_folder(f"tcloud/{str(SID).upper()}")[0] == SID
