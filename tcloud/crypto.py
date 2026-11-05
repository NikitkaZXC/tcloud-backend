"""Шифрование частей файла.

Каждая часть (кусок файла до ~2 ГБ, который уходит в Telegram одним документом)
устроена так:

    [заголовок 45 байт][сегмент 0][сегмент 1]...[сегмент N]

Заголовок: magic "TCLD", версия, id файла, соль, номер части, размер сегмента.
Сегмент — это AES-256-GCM от куска открытых данных (по умолчанию 4 МиБ) + 16 байт тега.

- Ключ файла = HKDF(мастер-ключ, соль файла). У каждого файла свой ключ.
- nonce = номер части + номер сегмента, поэтому он никогда не повторяется в рамках ключа.
- В AAD входят заголовок, номер сегмента и флаг «последний сегмент». Поэтому нельзя
  незаметно переставить, подменить или обрезать сегменты и части.
- Соль лежит в заголовке, так что файл можно расшифровать даже без локальной базы,
  имея только мастер-ключ.
"""

from __future__ import annotations

import base64
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"TCLD"
VERSION = 1
HEADER = struct.Struct(">4sB16s16sII")  # magic, version, file_id, salt, part_index, segment_size
TAG_SIZE = 16
KEY_SIZE = 32
SALT_SIZE = 16


class CryptoError(Exception):
    """Данные повреждены, подменены или зашифрованы другим ключом."""


def generate_master_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(KEY_SIZE)).decode()


def load_master_key(value: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(value.strip().encode())
    except Exception as exc:
        raise ValueError("TCLOUD_MASTER_KEY must be base64") from exc
    if len(key) != KEY_SIZE:
        raise ValueError(f"TCLOUD_MASTER_KEY must decode to {KEY_SIZE} bytes, got {len(key)}")
    return key


def new_salt() -> bytes:
    return os.urandom(SALT_SIZE)


def derive_file_key(master_key: bytes, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=KEY_SIZE, salt=salt, info=b"tcloud/v1/file-key").derive(master_key)


def segments_in(plain_size: int, segment_size: int) -> int:
    """Сколько сегментов в части с таким объёмом открытых данных (пустая часть = 1 сегмент)."""
    return max(1, -(-plain_size // segment_size))


def encrypted_part_size(plain_size: int, segment_size: int) -> int:
    return HEADER.size + plain_size + segments_in(plain_size, segment_size) * TAG_SIZE


def _nonce(part_index: int, segment_index: int) -> bytes:
    return struct.pack(">IQ", part_index, segment_index)


def _aad(header: bytes, segment_index: int, last: bool) -> bytes:
    return header + struct.pack(">QB", segment_index, 1 if last else 0)


class PartEncryptor:
    def __init__(self, master_key: bytes, file_id: bytes, salt: bytes, part_index: int, segment_size: int):
        self.header = HEADER.pack(MAGIC, VERSION, file_id, salt, part_index, segment_size)
        self._aes = AESGCM(derive_file_key(master_key, salt))
        self._part_index = part_index
        self._segment_size = segment_size
        self._segment_index = 0
        self._finished = False

    def encrypt_segment(self, data: bytes, last: bool) -> bytes:
        if self._finished:
            raise RuntimeError("part already finished")
        if len(data) > self._segment_size or (not last and len(data) != self._segment_size):
            raise ValueError("only the last segment may be shorter than segment_size")
        aad = _aad(self.header, self._segment_index, last)
        out = self._aes.encrypt(_nonce(self._part_index, self._segment_index), data, aad)
        self._segment_index += 1
        self._finished = last
        return out


class PartDecryptor:
    """Потоковый расшифровщик одной части: кормим его байтами в любом порядке нарезки."""

    def __init__(
        self,
        master_key: bytes,
        expected_file_id: bytes | None = None,
        expected_part_index: int | None = None,
        expected_segment_size: int | None = None,
    ):
        self._master_key = master_key
        self._expected = (expected_file_id, expected_part_index, expected_segment_size)
        self._buf = bytearray()
        self._header: bytes | None = None
        self._aes: AESGCM | None = None
        self._part_index = 0
        self._segment_size = 0
        self._segment_index = 0
        self.file_id: bytes | None = None

    def _parse_header(self) -> None:
        raw = bytes(self._buf[: HEADER.size])
        del self._buf[: HEADER.size]
        magic, version, file_id, salt, part_index, segment_size = HEADER.unpack(raw)
        if magic != MAGIC:
            raise CryptoError("not a TCloud part")
        if version != VERSION:
            raise CryptoError(f"unsupported part version {version}")
        exp_file, exp_part, exp_seg = self._expected
        if exp_file is not None and file_id != exp_file:
            raise CryptoError("part belongs to another file")
        if exp_part is not None and part_index != exp_part:
            raise CryptoError(f"expected part {exp_part}, got {part_index}")
        if exp_seg is not None and segment_size != exp_seg:
            raise CryptoError("segment size mismatch")
        if segment_size <= 0:
            raise CryptoError("bad segment size")
        self._header = raw
        self.file_id = file_id
        self._part_index = part_index
        self._segment_size = segment_size
        self._aes = AESGCM(derive_file_key(self._master_key, salt))

    def _decrypt(self, data: bytes, last: bool) -> bytes:
        assert self._aes is not None and self._header is not None
        try:
            out = self._aes.decrypt(
                _nonce(self._part_index, self._segment_index), data, _aad(self._header, self._segment_index, last)
            )
        except InvalidTag as exc:
            raise CryptoError(f"segment {self._segment_index} of part {self._part_index} failed authentication") from exc
        self._segment_index += 1
        return out

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        if self._header is None:
            if len(self._buf) < HEADER.size:
                return []
            self._parse_header()
        out: list[bytes] = []
        full = self._segment_size + TAG_SIZE
        # Полный сегмент расшифровываем, только если за ним есть ещё данные:
        # иначе он может оказаться последним, а это влияет на AAD.
        while len(self._buf) > full:
            chunk = bytes(self._buf[:full])
            del self._buf[:full]
            out.append(self._decrypt(chunk, last=False))
        return out

    def finish(self) -> list[bytes]:
        if self._header is None:
            raise CryptoError("part is truncated (no header)")
        if len(self._buf) < TAG_SIZE:
            raise CryptoError("part is truncated")
        chunk = bytes(self._buf)
        self._buf.clear()
        return [self._decrypt(chunk, last=True)]
