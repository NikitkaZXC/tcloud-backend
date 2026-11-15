import re

from src.utilities.parsers.exception import BadRangeError


def parse_range(header: str, size: int) -> tuple[int, int]:
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or match.groups() == ("", ""):
        raise BadRangeError("only a single bytes=start-end range is supported")
    first, last = match.groups()
    if first:
        start, end = int(first), min(int(last), size - 1) if last else size - 1
    else:
        start, end = max(size - int(last), 0), size - 1
    if start > end or start >= size:
        raise BadRangeError(f"range is outside of the file ({size} bytes)")
    return start, end
