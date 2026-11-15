import pytest

from src.utilities.parsers import BadRangeError, parse_range


@pytest.mark.parametrize(
    "header,size,expected",
    [
        ("bytes=0-9", 100, (0, 9)),
        ("bytes=90-", 100, (90, 99)),
        ("bytes=-10", 100, (90, 99)),
        ("bytes=-500", 100, (0, 99)),
        ("bytes=50-500", 100, (50, 99)),
    ],
)
def test_parse_range(header, size, expected):
    assert parse_range(header, size) == expected


@pytest.mark.parametrize(
    "header", ["bytes=-", "bytes=5-1", "bytes=100-", "items=0-1", "bytes=0-1,5-6"]
)
def test_parse_range_bad(header):
    with pytest.raises(BadRangeError):
        parse_range(header, 100)
