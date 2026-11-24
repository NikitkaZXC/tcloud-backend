import base64
import os

import pytest
from pydantic import SecretStr, ValidationError

from src.config import settings
from src.config.models import AppSettings
from src.infrastructure.security import (
    HEADER,
    DecryptionError,
    EncryptionUnavailableError,
    cipher_size,
    cipher_span,
    decrypt,
    encrypt_file,
    part_spans,
)
from src.infrastructure.security.encryption import BLOCK, TAG


async def chunks(data: bytes, size: int = 10_000):
    for start in range(0, len(data), size):
        yield data[start : start + size]


def encrypted(tmp_path, data: bytes) -> tuple[bytes, bytes]:
    source, target = tmp_path / "plain", tmp_path / "cipher"
    source.write_bytes(data)
    header = encrypt_file(source, target)
    return header, target.read_bytes()


async def read(header, cipher, size, start, end) -> bytes:
    offset, length = cipher_span(size, start, end)
    body = decrypt(chunks(cipher[offset : offset + length]), header, size, start, end)
    return b"".join([part async for part in body])


@pytest.mark.parametrize("size", [0, 1, BLOCK - 1, BLOCK, BLOCK + 1, 3 * BLOCK + 17])
async def test_round_trip_and_size(tmp_path, size):
    data = os.urandom(size)
    header, cipher = encrypted(tmp_path, data)
    assert len(cipher) == cipher_size(size) and cipher[:HEADER] == header
    assert await read(header, cipher, size, 0, size - 1) == data
    if size >= 16:
        assert data[:64] not in cipher


@pytest.mark.parametrize(
    "start,end",
    [(0, 0), (5, 5), (BLOCK - 1, BLOCK), (BLOCK, 2 * BLOCK - 1), (100, 3 * BLOCK + 16)],
)
async def test_any_range(tmp_path, start, end):
    data = os.urandom(3 * BLOCK + 17)
    header, cipher = encrypted(tmp_path, data)
    assert await read(header, cipher, len(data), start, end) == data[start : end + 1]


async def test_tampered_block_is_rejected(tmp_path):
    data = os.urandom(2 * BLOCK)
    header, cipher = encrypted(tmp_path, data)
    damaged = bytearray(cipher)
    damaged[HEADER + BLOCK + 100] ^= 1
    with pytest.raises(DecryptionError):
        await read(header, bytes(damaged), len(data), 0, len(data) - 1)


async def test_truncated_file_is_rejected(tmp_path):
    data = os.urandom(2 * BLOCK)
    header, cipher = encrypted(tmp_path, data)
    cut = cipher[: HEADER + BLOCK + 16]
    padded = cut + cipher[HEADER : HEADER + BLOCK + 16]
    with pytest.raises(DecryptionError):
        await read(header, cut, len(data), 0, len(data) - 1)
    with pytest.raises(DecryptionError):
        await read(header, padded, len(data), 0, len(data) - 1)


async def test_wrong_key_is_rejected(tmp_path, monkeypatch):
    header, cipher = encrypted(tmp_path, b"secret")
    other = SecretStr(base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(settings.app, "ENCRYPTION_KEY", other)
    with pytest.raises(DecryptionError):
        await read(header, cipher, 6, 0, 5)


def test_no_key_means_no_encryption(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.app, "ENCRYPTION_KEY", None)
    (tmp_path / "plain").write_bytes(b"x")
    with pytest.raises(EncryptionUnavailableError):
        encrypt_file(tmp_path / "plain", tmp_path / "cipher")


def test_bad_key_fails_at_start():
    with pytest.raises(ValidationError):
        AppSettings(SECRET_KEY="x" * 32, ENCRYPTION_KEY="short")
    assert AppSettings(SECRET_KEY="x" * 32, ENCRYPTION_KEY="").encryption_key is None


def test_part_spans_cover_the_file_within_the_limit():
    assert part_spans(100, 100, False) == [(0, 100)]
    assert part_spans(250, 100, False) == [(0, 100), (100, 100), (200, 50)]
    for limit in (2000 * 1024**2, BLOCK * 10):
        size = cipher_size(limit * 3)
        spans = part_spans(size, limit, True)
        assert spans[0][0] == 0 and sum(length for _, length in spans) == size
        assert all(length <= limit for _, length in spans)
        assert all((offset - HEADER) % (BLOCK + TAG) == 0 for offset, _ in spans[1:])
        assert all(a + b == c for (a, b), (c, _) in zip(spans, spans[1:]))


async def test_parts_decrypt_alone(tmp_path):
    source, target = tmp_path / "plain", tmp_path / "cipher"
    plain = os.urandom(BLOCK * 5 + 123)
    source.write_bytes(plain)
    header = encrypt_file(source, target)
    cipher = target.read_bytes()
    spans = part_spans(len(cipher), HEADER + 2 * (BLOCK + TAG), True)
    assert len(spans) == 3
    offset, length = spans[1]
    start = (offset - HEADER) // (BLOCK + TAG) * BLOCK
    end = start + 2 * BLOCK - 1
    assert (offset, length) == cipher_span(len(plain), start, end)
    got = b"".join(
        [
            chunk
            async for chunk in decrypt(
                chunks(cipher[offset : offset + length]), header, len(plain), start, end
            )
        ]
    )
    assert got == plain[start : end + 1]
