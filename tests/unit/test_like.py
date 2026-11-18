import pytest

from src.infrastructure.database.repositories.like import escape_like


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("plain", "plain"),
        ("100%", "100\\%"),
        ("a_b", "a\\_b"),
        ("back\\slash", "back\\\\slash"),
        ("\\%", "\\\\\\%"),
    ],
)
def test_escape_like(raw, escaped):
    assert escape_like(raw) == escaped
