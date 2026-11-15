# Разработка

Python 3.14, зависимости — `uv` (`pyproject.toml`, `uv.lock`). Группы: `test` (pytest, testcontainers, pip-audit),
`dev` = `test` + black, isort, locust (ставится по умолчанию). Пакет добавлять через `uv add` / `uv add --group test|dev`.

```bash
make install        # uv sync
make env            # .env из .env.exemple + сгенерированные APP_API_KEY и POSTGRES_PASSWORD
make dev-up         # Postgres (и ClamAV через docker-compose.dev.yml) на localhost
make app            # uvicorn с --reload на 127.0.0.1:8000 (для локального запуска в .env: POSTGRES_HOST=127.0.0.1, CLAMAV_HOST=127.0.0.1)
make login          # вход в Telegram (телефон, код, 2FA)
make format         # black + isort
make check          # pip-audit + проверка миграций
uv run alembic revision --autogenerate -m "msg"
```

Локально без ffmpeg тесты, которым он нужен, пропускаются — полный прогон `make test-docker`.

## Настройки

`src/config/models/*`, по классу на префикс: `APP_`, `POSTGRES_`, `TELEGRAM_`, `CLAMAV_`, `UPLOAD_`, `LOG_`.
Читаются из `.env` (путь меняется переменной `ENV_FILE`, пусто — не читать, как в тестах), переменные окружения важнее.
