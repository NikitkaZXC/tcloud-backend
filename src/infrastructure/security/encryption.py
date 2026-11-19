import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.config import settings
from src.infrastructure.security.exception import (
    DecryptionError,
    EncryptionUnavailableError,
)

MAGIC = b"TCE1"
BLOCK = 64 * 1024
TAG = 16
KEY = 32
NONCE = 12
PREFIX = 7
WRAPPED = NONCE + KEY + TAG
HEADER = len(MAGIC) + WRAPPED + PREFIX
SEALED = b"TCE1-thumbnail"


def _kek() -> AESGCM:
    key = settings.app.encryption_key
    if key is None:
        raise EncryptionUnavailableError(
            "encryption is not configured on the server (APP_ENCRYPTION_KEY)"
        )
    return AESGCM(key)


def check_available() -> None:
    _kek()


def blocks(plain_size: int) -> int:
    return max(1, -(-plain_size // BLOCK))


def cipher_size(plain_size: int) -> int:
    return HEADER + plain_size + TAG * blocks(plain_size)


def plain_limit(limit: int) -> int:
    return limit - HEADER - TAG * blocks(limit)


def _nonce(prefix: bytes, index: int, last: bool) -> bytes:
    return prefix + index.to_bytes(4, "big") + bytes([last])


def encrypt_file(
    source: Path, target: Path, progress: Callable[[int], None] | None = None
) -> bytes:
    dek, wrap_nonce, prefix = (
        AESGCM.generate_key(256),
        os.urandom(NONCE),
        os.urandom(PREFIX),
    )
    header = MAGIC + wrap_nonce + _kek().encrypt(wrap_nonce, dek, MAGIC) + prefix
    cipher = AESGCM(dek)
    with source.open("rb") as plain, target.open("wb") as output:
        target.chmod(0o600)
        output.write(header)
        index, block = 0, plain.read(BLOCK)
        while True:
            following = plain.read(BLOCK)
            last = not following
            output.write(cipher.encrypt(_nonce(prefix, index, last), block, None))
            if progress:
                progress(index * BLOCK + len(block))
            if last:
                return header
            index, block = index + 1, following


def _open(header: bytes) -> tuple[AESGCM, bytes]:
    if len(header) != HEADER or not header.startswith(MAGIC):
        raise DecryptionError("encrypted file header is damaged")
    wrap_nonce = header[len(MAGIC) : len(MAGIC) + NONCE]
    wrapped = header[len(MAGIC) + NONCE : len(MAGIC) + WRAPPED]
    try:
        dek = _kek().decrypt(wrap_nonce, wrapped, MAGIC)
    except InvalidTag as error:
        raise DecryptionError("the server key does not open this file") from error
    return AESGCM(dek), header[-PREFIX:]


def seal(header: bytes, data: bytes) -> bytes:
    cipher, _ = _open(header)
    nonce = os.urandom(NONCE)
    return nonce + cipher.encrypt(nonce, data, SEALED)


def unseal(header: bytes, data: bytes) -> bytes:
    cipher, _ = _open(header)
    try:
        return cipher.decrypt(data[:NONCE], data[NONCE:], SEALED)
    except InvalidTag as error:
        raise DecryptionError("the encrypted thumbnail is damaged") from error


def cipher_span(plain_size: int, start: int, end: int) -> tuple[int, int]:
    first, last = start // BLOCK, end // BLOCK if plain_size else 0
    offset = HEADER + first * (BLOCK + TAG)
    stop = HEADER + min(plain_size, (last + 1) * BLOCK) + (last + 1) * TAG
    return offset, stop - offset


def decrypt(
    chunks: AsyncIterator[bytes], header: bytes, plain_size: int, start: int, end: int
) -> AsyncIterator[bytes]:
    cipher, prefix = _open(header)
    return _decrypt(chunks, cipher, prefix, plain_size, start, end)


async def _decrypt(
    chunks: AsyncIterator[bytes],
    cipher: AESGCM,
    prefix: bytes,
    plain_size: int,
    start: int,
    end: int,
) -> AsyncIterator[bytes]:
    total = blocks(plain_size)
    index, last = start // BLOCK, end // BLOCK if plain_size else 0
    skip = start - index * BLOCK
    buffer = bytearray()

    def block_size(position: int) -> int:
        return min(BLOCK, plain_size - position * BLOCK) + TAG

    async for chunk in chunks:
        buffer += chunk
        while index <= last and len(buffer) >= block_size(index):
            size = block_size(index)
            try:
                plain = cipher.decrypt(
                    _nonce(prefix, index, index == total - 1),
                    bytes(buffer[:size]),
                    None,
                )
            except InvalidTag as error:
                raise DecryptionError(
                    "the encrypted file in Telegram is damaged or replaced"
                ) from error
            del buffer[:size]
            if index == last:
                plain = plain[: end - index * BLOCK + 1]
            yield plain[skip:]
            skip = 0
            index += 1
    if index <= last:
        raise DecryptionError("the encrypted file in Telegram is truncated")
