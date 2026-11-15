# Развёртывание

`docker-compose.prod.yml`: `app`, `postgres:18`, `clamav`. Образ — `python:3.14-slim` + ffmpeg + uv,
только runtime-зависимости, пользователь `appuser`. `scripts/docker-entrypoint.sh` применяет миграции
(`RUN_MIGRATIONS=0` — пропустить) и запускает uvicorn.

```bash
make env            # заполнить TELEGRAM_API_ID / TELEGRAM_API_HASH в .env
make login          # один раз; сессия сохранится в volume session
make up             # сборка и запуск
make logs
```

- Порт — `127.0.0.1:8000`. Для доступа снаружи — reverse proxy с TLS.
- Один процесс uvicorn (см. background-tasks.md). Масштабирование — вертикальное: `TELEGRAM_CONCURRENCY`,
  `TELEGRAM_UPLOAD_WORKERS`, `UPLOAD_SCAN_CONCURRENCY`.
- Volume `tmp` должен вмещать файлы, которые грузятся одновременно (до 4 ГБ каждый).
- Первый старт ClamAV качает базы несколько минут — до этого загрузки отвечают `503 scanner_unavailable`.
- Пароль Postgres задаётся при первом создании volume `pgdata`; сменить его в `.env` позже — значит пересоздать volume.
