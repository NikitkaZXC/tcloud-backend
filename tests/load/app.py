import os

import main
from tests.fakes import FakeScanner, FakeTelegram

telegram = FakeTelegram()
telegram.delay = float(os.environ.get("LOAD_TELEGRAM_DELAY", "0.2"))
main.create_telegram = lambda: telegram
main.create_scanner = FakeScanner

app = main.app
