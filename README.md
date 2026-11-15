# tcloud

Хранилище медиа поверх Telegram с REST API: фото, видео, аудио и GIF уходят в приватные каналы (хранилища)
через Telethon, у каждого файла свой UUID и путь `tcloud/{хранилище}/{папки}/{файл}`.

- Лимит файла по аккаунту: 2 ГБ, с Premium 4 ГБ. Отправка оригиналом или сжатым медиа.
- Загрузка с устройства, по ссылке и пачками до 100 файлов; прогресс, место в очереди, вебхуки.
- Сотни загрузок разом: лимиты по стадиям, резерв диска, пауза при FloodWait, параллельная выгрузка кусков.
- Проверка типа по содержимому, ClamAV, защита от SSRF.
- FastAPI, PostgreSQL, Python 3.14 (uv), всё в Docker.

## Быстрый старт

```bash
make env          # .env + сгенерированные ключи; заполнить TELEGRAM_API_ID / TELEGRAM_API_HASH
make login        # вход в Telegram, один раз
make up           # API на 127.0.0.1:8000, Swagger — /docs
```

```bash
curl -H "X-API-Key: $APP_API_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"Фото"}' http://127.0.0.1:8000/api/storages
```

## Документация

Всё подробно — в [`docs/`](docs/README.md): архитектура, база, безопасность, Telegram и его лимиты, фоновая обработка,
разработка, развёртывание, тесты и процессы. Исходное ТЗ — [`docs/TZ.md`](docs/TZ.md).

## Тесты

```bash
make test-all       # локально (Postgres через testcontainers)
make test-docker    # всё в Docker, с ffmpeg
make load-up && make load-app & make load   # нагрузка (locust)
```
