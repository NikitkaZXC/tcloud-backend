import os
import tempfile

TEST_ENV = {
    "ENV_FILE": "",
    "APP_API_KEY": "test",
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_DB": "tcloud_test",
    "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test",
    "TELEGRAM_API_ID": "1",
    "TELEGRAM_API_HASH": "test",
    "TELEGRAM_CHATS": "",
    "TELEGRAM_SESSION": os.path.join(
        tempfile.mkdtemp(prefix="tcloud-tests-"), "session"
    ),
    "UPLOAD_TMP_DIR": tempfile.mkdtemp(prefix="tcloud-tmp-"),
    "UPLOAD_MIN_FREE_DISK_MB": "0",
    "UPLOAD_RETRY_DELAY": "0.01",
    "LOG_LEVEL": "WARNING",
}
os.environ.update(TEST_ENV)

pytest_plugins = ["tests.fixtures.infrastructure", "tests.fixtures.media"]
