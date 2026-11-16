# Разработка

Python 3.14, зависимости — `uv` (`pyproject.toml`, `uv.lock`). Группы: `test` (pytest, testcontainers, pip-audit),
`dev` = `test` + black, isort, locust (ставится по умолчанию). Пакет добавлять через `uv add` / `uv add --group test|dev`.

```bash
make install        # uv sync
make env            # .env из .env.exemple + сгенерированные APP_API_KEY и пароли Postgres, RabbitMQ, Redis
make dev-up         # Postgres, RabbitMQ (панель :15672), Redis на localhost; ClamAV — docker-compose.dev.yml
make app            # uvicorn с --reload на 127.0.0.1:8000 (в .env: POSTGRES_HOST, CLAMAV_HOST, RABBITMQ_HOST, REDIS_HOST=127.0.0.1)
make worker-prepare # Celery-воркер очереди uploads.prepare
make worker-telegram  # Celery-воркер очереди uploads.telegram
make login          # вход в Telegram (телефон, код, 2FA) — дважды: сессии API и воркера
make format         # black + isort
make check          # pip-audit + проверка миграций
uv run alembic revision --autogenerate -m "msg"
```

Локально без ffmpeg тесты, которым он нужен, пропускаются — полный прогон `make test-docker`.

## Настройки

`src/config/models/*`, по классу на префикс: `APP_`, `POSTGRES_`, `TELEGRAM_`, `CLAMAV_`, `UPLOAD_`, `RABBITMQ_`, `REDIS_`, `LOG_`.
Читаются из `.env` (путь меняется переменной `ENV_FILE`, пусто — не читать, как в тестах), переменные окружения важнее.
