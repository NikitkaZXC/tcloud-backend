# Разработка

Python 3.14, зависимости — `uv` (`pyproject.toml`, `uv.lock`). Группы: `test` (pytest, testcontainers, pip-audit),
`dev` = `test` + black, isort, locust (ставится по умолчанию). Пакет добавлять через `uv add` / `uv add --group test|dev`.

```bash
make install        # uv sync
make env            # .env из .env.exemple + сгенерированные APP_SECRET_KEY, ROOT_PASSWORD, пароли Postgres, RabbitMQ, Redis и ключи JWT в certs/
make dev-up         # Postgres, RabbitMQ (панель :15672), Redis на localhost
docker compose -f docker-compose.dev.yml up -d clamav   # ClamAV на :3310 (make dev-up его не поднимает)
make app            # uvicorn с --reload на 127.0.0.1:8000 (в .env: POSTGRES_HOST, CLAMAV_HOST, RABBITMQ_HOST, REDIS_HOST=127.0.0.1)
make worker-download  # Celery-воркер очереди uploads.download
make worker-prepare   # Celery-воркер очереди uploads.prepare
make worker-telegram  # Celery-воркер очередей uploads.telegram и archives.build
make flower           # панель очередей на :5555
make format         # black + isort
make check          # pip-audit + проверка миграций
uv run alembic revision --autogenerate -m "msg"
```

Локально без ffmpeg тесты, которым он нужен, пропускаются — полный прогон `make test-docker`.

## Что запускать

| Задача | Команды | Где код |
|---|---|---|
| Пользоваться сервисом | `make up` (и `make logs`) | всё в Docker, `docker-compose.prod.yml` |
| Править код с автоперезагрузкой | `make dev-up`, затем `make app` и воркеры `make worker-*` | локально, в Docker только Postgres, RabbitMQ, Redis |
| Всё в Docker из исходников | `make dev-up-all` | `docker-compose.dev.yml`, профиль `app` |

После любого варианта — вход как root (`POST /api/auth/signin`, flows/users.md) и вход в Telegram на `/docs`, раздел **Telegram** (flows/auth.md). Локально сессии и временные
файлы лежат в `./data/` (пути по умолчанию `data/session/...`, `data/tmp`; в Docker compose задаёт `/data/...`).
Для `make app` хосты в `.env` должны быть `127.0.0.1` (`POSTGRES_HOST`, `CLAMAV_HOST`, `RABBITMQ_HOST`, `REDIS_HOST`),
compose подставляет свои сам.

## Настройки

`src/config/models/*`, по классу на префикс: `APP_`, `POSTGRES_`, `TELEGRAM_`, `CLAMAV_`, `UPLOAD_`, `RABBITMQ_`, `REDIS_`, `LOG_`.
Читаются из `.env` (путь меняется переменной `ENV_FILE`, пусто — не читать, как в тестах), переменные окружения важнее.
