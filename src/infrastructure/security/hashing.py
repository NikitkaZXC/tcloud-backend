import asyncio
import hashlib
import hmac
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.config import settings

_hasher = PasswordHasher()
DUMMY_HASH = _hasher.hash("dummy-password")


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_hasher.hash, password)


def _verify(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False


async def verify_password(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(_verify, password, password_hash)


def hash_secret(value: str) -> str:
    return hmac.new(
        settings.app.SECRET_KEY.get_secret_value().encode(),
        value.encode(),
        hashlib.sha256,
    ).hexdigest()


def webhook_key(user_id: UUID | str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=f"tcloud-webhook:{user_id}".encode(),
    ).derive(settings.app.SECRET_KEY.get_secret_value().encode())
