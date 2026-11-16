import os

import main
from src.infrastructure.enums import TelegramSession
from src.infrastructure.integrations.telegram.factory import session_path
from src.infrastructure.integrations.telegram.login import session_file
from tests.fakes import FakeScanner, FakeTelegram

worker_session = session_file(session_path(TelegramSession.WORKER))
worker_session.parent.mkdir(parents=True, exist_ok=True)
worker_session.touch()

telegram = FakeTelegram()
telegram.delay = float(os.environ.get("LOAD_TELEGRAM_DELAY", "0.2"))
main.create_telegram = lambda *args, **kwargs: telegram
main.create_scanner = FakeScanner

app = main.app
