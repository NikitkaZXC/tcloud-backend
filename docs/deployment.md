# Развёртывание

`docker-compose.prod.yml`: `app` (API), `worker-prepare`, `worker-telegram`, `postgres:18`, `rabbitmq:4`, `redis:8`, `clamav`.
Образ один на API и воркеры — `python:3.14-slim` + ffmpeg + uv, только runtime-зависимости, пользователь `appuser`.
`scripts/docker-entrypoint.sh` применяет миграции (у воркеров `RUN_MIGRATIONS=0`) и запускает команду сервиса.

```bash
make env            # заполнить TELEGRAM_API_ID / TELEGRAM_API_HASH в .env; пароли RabbitMQ и Redis сгенерируются
make login          # один раз; вход дважды — сессии API и worker-telegram, обе в volume session
make up             # сборка и запуск
make logs
```

- Порт — `127.0.0.1:8000`. Для доступа снаружи — reverse proxy с TLS.
- `worker-telegram` — ровно одна реплика (общая пауза FloodWait и одна сессия); параллельность —
  `TELEGRAM_CONCURRENCY` и `TELEGRAM_UPLOAD_WORKERS`. `worker-prepare` можно масштабировать репликами
  (`docker compose up --scale worker-prepare=2`), потоков на реплику — `UPLOAD_PREPARE_CONCURRENCY`.
- Volume `tmp` общий для API и воркеров и должен вмещать файлы, которые ждут в очереди (до 4 ГБ каждый).
- RabbitMQ: `consumer_timeout` поднят до 24 ч, иначе брокер закрывает канал на долгой выгрузке (по умолчанию 30 мин).
  Очереди durable, сообщения persistent; Redis с `appendonly yes` — очередь и статусы переживают рестарт.
- Первый старт ClamAV качает базы несколько минут — до этого загрузки отвечают `503 scanner_unavailable`.
- Пароль Postgres задаётся при первом создании volume `pgdata`; сменить его в `.env` позже — значит пересоздать volume.
