# Развёртывание

`docker-compose.prod.yml`: `app` (API), `worker-download`, `worker-prepare`, `worker-telegram`, `flower`, `postgres:18`,
`rabbitmq:4`, `redis:8`, `clamav`.
Образ один на API и воркеры — `python:3.14-slim` + ffmpeg + uv, только runtime-зависимости, пользователь `appuser`.
`scripts/docker-entrypoint.sh` применяет миграции (у воркеров `RUN_MIGRATIONS=0`) и запускает команду сервиса.

```bash
make env            # заполнить TELEGRAM_API_ID / TELEGRAM_API_HASH в .env; пароли RabbitMQ, Redis и Flower сгенерируются
make up             # сборка и запуск
make logs
```

Затем вход как root (`ROOT_LOGIN`/`ROOT_PASSWORD`, `POST /api/auth/signin`) и вход в Telegram через Swagger `http://127.0.0.1:8000/docs`, раздел **Telegram** — отдельно для сессий `api`, `upload` и `download`
(flows/auth.md). Файлы сессий лежат в volume `session` и переживают пересборку; входить заново нужно, только если
volume удалён или сессию завершили в Telegram. Без входа приложение работает, но Telegram-запросы отвечают
`503 telegram_unauthorized`.

- Порт — `127.0.0.1:8000`. Для доступа снаружи — reverse proxy с TLS: фронтенд на `site.com`, на бэк — `/api/*`, `/docs`, `/redoc`, `/openapi.json`, с сохранением `Host` (иначе проверка `Origin` отклонит запросы, см. security.md).
- `make env` кладёт RS256-ключи JWT в `certs/` (в git и образ не попадают), compose монтирует их в `/certs` read-only.
  Куки `Secure` (`JWT_SECURE=true`): снаружи нужен TLS. Приложение в контейнере — uid 1000: на Linux, если вы не uid 1000,
  выполните `sudo chown 1000 certs/jwt-private.pem`, иначе API не стартует (`JWT key ... is not readable`).
- `worker-telegram` — ровно одна реплика (общая пауза FloodWait и одна сессия), слушает `uploads.telegram` и
  `archives.build` (сборка архивов папок); параллельность —
  `TELEGRAM_CONCURRENCY` и `TELEGRAM_UPLOAD_WORKERS`. `worker-download` и `worker-prepare` можно масштабировать
  репликами (`docker compose up --scale worker-prepare=2`), потоков на реплику — `UPLOAD_DOWNLOAD_CONCURRENCY` и
  `UPLOAD_PREPARE_CONCURRENCY`.
- Flower — `127.0.0.1:5555` с basic auth (`FLOWER_USER` / `FLOWER_PASSWORD`); наружу — только через reverse proxy с TLS.
- `GET /api/health` с входом (кука или `X-API-Key`) показывает полный отчёт: БД, брокер, живые воркеры по очередям, авторизацию сессий
  (`telegram`, `telegram_upload`, `telegram_download`), ClamAV, Redis, общий `ok` и `details` (время каждой проверки `latency_ms`, возраст heartbeat воркеров `last_seen_s`, background-tasks.md); ответ всегда `200`. Без входа — только
  `{"status": "ok"}` с `200` или `{"status": "degraded"}` с `503` (то же `ok`, без подробностей) — подходит для внешнего
  мониторинга и балансировщика. Неверные учётные данные → `401`, как у остальных ручек.
- Volume `tmp` общий для API и воркеров и должен вмещать файлы, которые ждут в очереди (до 4 ГБ каждый).
- RabbitMQ: `consumer_timeout` поднят до 24 ч, иначе брокер закрывает канал на долгой выгрузке (по умолчанию 30 мин).
  Очереди durable, сообщения persistent; Redis с `appendonly yes` — очередь и статусы переживают рестарт.
- Первый старт ClamAV качает базы несколько минут — до этого загрузки отвечают `503 scanner_unavailable`.
- Пароль Postgres задаётся при первом создании volume `pgdata`; сменить его в `.env` позже — значит пересоздать volume.
- Reverse proxy перед API (nginx фронтенда): тела файлов нельзя сжимать — `GET /api/files/{id}/download` и
  `GET /api/tasks/{id}/archive` отдают `Cache-Control: no-transform` и точный `Content-Length` (на нём держатся `Range`
  и прогресс в браузере). nginx `no-transform` не учитывает, а `gzip_proxied` смотрит только на запросы с `Via`,
  поэтому gzip для этих путей выключается отдельным `location` с тем же проксированием, что и `/api/`:

  ```nginx
  location ~ ^/api/(files/[^/]+/(download|thumb)|tasks/[^/]+/archive)$ {
      gzip off;
      proxy_pass ${API_UPSTREAM};
      # те же proxy_set_header / proxy_buffering off / таймауты, что в location /api/
  }
  ```

  Документы и так приходят как `application/octet-stream`, а медиа и архивы — не `application/json`, так что при
  `gzip_types application/json` сейчас под сжатие ничего из этого не попадает; `location` — страховка на случай
  расширения `gzip_types`.
