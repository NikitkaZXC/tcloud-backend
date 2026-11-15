# CLAUDE.md

## Проект

TCloud — хранилище медиа в приватных Telegram-каналах через Telethon + FastAPI + PostgreSQL, всё в Docker.
Источник правды — [`docs/TZ.md`](docs/TZ.md), устройство кода — [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Правила работы

- Сначала план задачи, потом реализация по плану.
- Важные решения — вопросами с вариантами ответа; решает владелец.
- К новому функционалу — тесты (чем больше, тем лучше).
- Вместе с кодом обновлять `docs/`, `README.md` и этот файл.
- Новые запросы к БД — с индексами. Все наши id — UUID. Схема меняется только миграцией Alembic.
- Учитывать лимиты Telegram (ТЗ §3) — они в `tcloud/limits.py`.
- Коммитить каждое изменение, **никогда не пушить**.

## Команды

```bash
docker compose --profile test run --rm tests                      # тесты (Postgres + ffmpeg в Docker)
docker compose --profile test run --rm tests alembic check        # модели совпадают с миграциями?
docker compose --profile test run --rm tests pip-audit -r requirements.txt
docker compose run --rm --no-deps app python -m tcloud login      # вход в Telegram
docker compose up -d                                              # запуск
```

Локальный `.venv` (Python 3.10) для проекта не используется — всё через Docker (Python 3.12).

## Подводные камни

- Telethon определяет тип файла по расширению — временные файлы получают расширение из имени.
- ClamAV: образ `*-debian` (alpine-теги только amd64), лимиты потока — `docker/clamd.conf`.
- В тестах `Settings(_env_file=None, ...)`, чтобы локальный `.env` не влиял.
