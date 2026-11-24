import os
import tempfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

SESSIONS = tempfile.mkdtemp(prefix="tcloud-tests-")
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
with open(os.path.join(SESSIONS, "jwt-private.pem"), "wb") as pem:
    pem.write(
        KEY.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
with open(os.path.join(SESSIONS, "jwt-public.pem"), "wb") as pem:
    pem.write(
        KEY.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
TEST_ENV = {
    "ENV_FILE": "",
    "APP_SECRET_KEY": "test-secret-key-0123456789abcdefghij",
    "APP_ENCRYPTION_KEY": "dGNsb3VkLXRlc3QtZW5jcnlwdGlvbi1rZXktMzJieXQ=",
    "APP_ALLOWED_ORIGINS": '["http://front.test"]',
    "ROOT_LOGIN": "root",
    "ROOT_PASSWORD": "root-password",
    "JWT_PRIVATE_KEY_PATH": os.path.join(SESSIONS, "jwt-private.pem"),
    "JWT_PUBLIC_KEY_PATH": os.path.join(SESSIONS, "jwt-public.pem"),
    "JWT_SECURE": "false",
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_DB": "tcloud_test",
    "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test",
    "TELEGRAM_API_ID": "1",
    "TELEGRAM_API_HASH": "test",
    "TELEGRAM_CHATS": "",
    "TELEGRAM_SESSION": os.path.join(SESSIONS, "session"),
    "TELEGRAM_UPLOAD_SESSION": os.path.join(SESSIONS, "upload"),
    "TELEGRAM_DOWNLOAD_SESSION": os.path.join(SESSIONS, "download"),
    "UPLOAD_TMP_DIR": tempfile.mkdtemp(prefix="tcloud-tmp-"),
    "UPLOAD_MIN_FREE_DISK_MB": "0",
    "UPLOAD_MAX_FILES_ON_DISK": "1000",
    "UPLOAD_RETRY_DELAY": "0.01",
    "UPLOAD_HEARTBEAT_INTERVAL": "0.2",
    "LOG_LEVEL": "WARNING",
}
os.environ.update(TEST_ENV)

pytest_plugins = ["tests.fixtures.infrastructure", "tests.fixtures.media"]
