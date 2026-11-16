# tcloud

Файловое хранилище поверх Telegram с REST API: фото, видео, аудио, GIF и документы уходят в приватные каналы (хранилища)
через Telethon, у каждого файла свой UUID и путь `tcloud/{хранилище}/{папки}/{файл}`.

- Лимит файла по аккаунту: 2 ГБ, с Premium 4 ГБ. Отправка оригиналом или сжатым медиа.
- Загрузка с устройства одним запросом, по ссылке и пачками до 100 ссылок; прогресс, номер в очереди, вебхуки.
- Очереди Celery на RabbitMQ с приоритетом `low/normal/high`: скачивание, проверка и отправка в Telegram — отдельные
  воркеры, статусы в Redis. Отмена, смена приоритета, повтор, без дублей при падении воркера, Flower и health по воркерам.
- Проверка типа по содержимому, запрет исполняемых файлов (и внутри zip), строгий ClamAV, защита от SSRF.
- Удалили файл руками в Telegram — запись в БД пропадает сама (события Telethon, плюс проверка при скачивании).
- FastAPI, PostgreSQL, RabbitMQ, Redis, Celery, Python 3.14 (uv), всё в Docker.

## Быстрый старт

```bash
make env          # .env + сгенерированные ключи; заполнить TELEGRAM_API_ID / TELEGRAM_API_HASH
make login        # вход в Telegram, один раз (две сессии: API и воркер)
make up           # API на 127.0.0.1:8000, Swagger — /docs
```

```bash
curl -H "X-API-Key: $APP_API_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"Фото"}' http://127.0.0.1:8000/api/storages
curl -H "X-API-Key: $APP_API_KEY" --data-binary @photo.jpg \
  "http://127.0.0.1:8000/api/uploads?path=tcloud/<uuid>/отпуск/photo.jpg&priority=high"
```

## Документация

Всё подробно — в [`docs/`](docs/README.md): архитектура, база, безопасность, Telegram и его лимиты, фоновая обработка,
разработка, развёртывание, тесты и процессы. Исходное ТЗ — [`docs/TZ.md`](docs/TZ.md).

## Тесты

```bash
make test-all       # локально (Postgres, Redis, RabbitMQ через testcontainers)
make test-docker    # всё в Docker, с ffmpeg
make load-up && make load-app & make load   # нагрузка (locust)
```
