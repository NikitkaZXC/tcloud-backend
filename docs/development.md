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

CI (`.github/workflows/ci.yml`, на push в `main` и на pull request; `concurrency` отменяет прошлый прогон той же ветки): `lint` (black/isort `--check`), `test` (весь `pytest` с Postgres, Redis, RabbitMQ и ffmpeg), `audit` (pip-audit). Dependabot (`.github/dependabot.yml`) раз в месяц обновляет uv, Docker, docker-compose и GitHub Actions: все обновления экосистемы (major, minor, patch) в одной группе, то есть один PR на экосистему, не больше 2 открытых.

Токен CI только на чтение (`permissions: contents: read`). Actions закреплены по SHA коммита, а не по тегу (тег можно
перевесить); комментариев в YAML нет, соответствие — здесь. Dependabot обновляет SHA сам; при ручной смене версии SHA
берётся из `git ls-remote --tags https://github.com/{owner}/{repo} 'refs/tags/{tag}*'` (у аннотированного тега — строка
`^{}`, это и есть коммит) или `gh api repos/{owner}/{repo}/git/ref/tags/{tag}` (для `object.type = tag` ещё
`gh api repos/{owner}/{repo}/git/tags/{sha}`), и таблица ниже правится в том же коммите.

| Action | Тег | SHA |
|---|---|---|
| `actions/checkout` | `v7` (= `v7.0.1`) | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `astral-sh/setup-uv` | `v10.4.0` | `1db35dd8eee11c5894872e0b383ae6c406cf738d` |

Локально без ffmpeg тесты, которым он нужен, пропускаются — полный прогон `make test-docker`.

## Что запускать

| Задача | Команды | Где код |
|---|---|---|
| Пользоваться сервисом | `make up` (и `make logs`) | всё в Docker, `docker-compose.prod.yml` |
| Править код с автоперезагрузкой | `make dev-up`, затем `make app` и воркеры `make worker-*` | локально, в Docker только Postgres, RabbitMQ, Redis |
| Всё в Docker из исходников | `make dev-up-all` | `docker-compose.dev.yml`, профиль `app`: API, три воркера и Flower на `127.0.0.1:5555` (basic auth `FLOWER_USER` / `FLOWER_PASSWORD` из `.env`, без них `tcloud` / `tcloud`) |

После любого варианта — вход как root (`POST /api/auth/signin`, flows/users.md) и вход в Telegram на `/docs`, раздел **Telegram** (flows/auth.md; в `make up` Swagger выключен — страница `/telegram` фронтенда, deployment.md). Локально сессии и временные
файлы лежат в `./data/` (пути по умолчанию `data/session/...`, `data/tmp`; в Docker compose задаёт `/data/...`).
Для `make app` хосты в `.env` должны быть `127.0.0.1` (`POSTGRES_HOST`, `CLAMAV_HOST`, `RABBITMQ_HOST`, `REDIS_HOST`),
compose подставляет свои сам.

## Миграции

Alembic, `src/infrastructure/database/migrations/` (список ревизий — database.md). В Docker их применяет
`scripts/docker-entrypoint.sh` при старте `app` (у воркеров `RUN_MIGRATIONS=0`), локально — `uv run alembic upgrade head`.
Новая ревизия: `uv run alembic revision --autogenerate -m "msg"`; существующие не правим.

## Пример со скриптом

API-ключ: `POST /api/users/{id}/api-keys` (flows/users.md).

```bash
curl -H "X-API-Key: $TCLOUD_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"Фото"}' http://127.0.0.1:8000/api/storages
curl -H "X-API-Key: $TCLOUD_KEY" --data-binary @photo.jpg \
  "http://127.0.0.1:8000/api/uploads?path=tcloud/<uuid>/отпуск/photo.jpg&priority=high"
```

## Настройки

`src/config/models/*`, по классу на префикс: `APP_`, `POSTGRES_`, `JWT_`, `ROOT_`, `RATE_LIMIT_`, `TELEGRAM_`, `CLAMAV_`, `UPLOAD_`, `RABBITMQ_`, `REDIS_`, `LOG_`.
Читаются из `.env` (путь меняется переменной `ENV_FILE`, пусто — не читать, как в тестах), переменные окружения важнее.
Шаблон — `.env.exemple`, `make env` заполняет секреты. Ошибки валидации не печатают введённые значения
(`hide_input_in_errors`), чтобы секреты не попадали в лог старта. Главное:

| Переменная | По умолчанию | Что |
|---|---|---|
| `APP_SECRET_KEY` | — (обязательно, ≥ 32 символов, `make env`) | HMAC refresh-токенов и API-ключей, из него HKDF выводит ключи подписи вебхуков пользователей; короче — настройки не загружаются |
| `APP_ENCRYPTION_KEY` | пусто — шифрование выключено | 32 байта base64, ключ шифрования файлов; потеря = потеря зашифрованных файлов |
| `APP_MAX_BODY_SIZE` | `1048576` (1 МиБ) | потолок тела запроса, кроме потоковых загрузок (`413 body_too_large`, security.md) |
| `APP_ALLOWED_ORIGINS` | `[]` | CORS и проверка `Origin` для фронтенда на другом origin |
| `ROOT_LOGIN`, `ROOT_PASSWORD` | — | root, синхронизируется при каждом старте API; `ROOT_PASSWORD` ≥ 12 символов, иначе настройки не загружаются |
| `JWT_SECURE` | `true` | куки только по HTTPS; `false` для http без TLS |
| `JWT_PRIVATE_KEY_PATH`, `JWT_PUBLIC_KEY_PATH` | `certs/jwt-*.pem` | ключи RS256 |
| `JWT_ACCESS_TOKEN_EXPIRATION_MINUTES` / `JWT_REFRESH_TOKEN_EXPIRATION_DAYS` | `15` / `30` | сроки токенов |
| `JWT_MAX_FAILED_LOGINS`, `JWT_LOCK_MINUTES` | `5`, `15` | блокировка аккаунта после неверных паролей |
| `RATE_LIMIT_ENABLED` | `true` | rate limiting (`429 rate_limited` + `Retry-After`), docs/security.md |
| `RATE_LIMIT_<ПРАВИЛО>__LIMIT`, `RATE_LIMIT_<ПРАВИЛО>__WINDOW` | см. docs/security.md | правило парой; `JWT_SIGNIN_IP_LIMIT`/`JWT_SIGNIN_IP_WINDOW` переименованы в `RATE_LIMIT_SIGNIN_IP__LIMIT`/`__WINDOW` |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | IP/подсеть reverse proxy, которому uvicorn верит в `X-Forwarded-For`; никогда `*` |
| `POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | `tcloud` для БД | PostgreSQL |
| `POSTGRES_POOL_RECYCLE` | `1800` | через сколько секунд пул API пересоздаёт соединение (`POSTGRES_POOL_PRE_PING` убран, docs/architecture.md) |
| `RABBITMQ_HOST`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD` | `rabbitmq`, `tcloud` | брокер |
| `REDIS_HOST`, `REDIS_PASSWORD`, `REDIS_PREFIX` | `redis`, пусто, `tcloud` | Redis ([docs/redis.md](docs/redis.md)) |
| `REDIS_CACHE_TTL` | `180` | срок кешей сведений из Telegram в Redis, с (каналы, аккаунт, аватарки, превью; docs/redis.md) |
| `REDIS_THUMB_CACHE_MB` | `64` | потолок памяти кеша превью открытых файлов в Redis (base64), `0` — без кеша |
| `CLAMAV_HOST`, `CLAMAV_PORT`, `CLAMAV_TIMEOUT` | `clamav`, `3310`, `900` | антивирус |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | — | приложение на my.telegram.org |
| `TELEGRAM_SESSION`, `TELEGRAM_UPLOAD_SESSION`, `TELEGRAM_DOWNLOAD_SESSION` | `data/session/tcloud*` (compose — `/data/session/...`) | файлы сессий `api`, `upload`, `download` |
| `TELEGRAM_CHATS` | пусто | существующие каналы, подключаемые как хранилища при старте |
| `TELEGRAM_CONCURRENCY`, `TELEGRAM_UPLOAD_WORKERS`, `TELEGRAM_FLOOD_MAX_WAIT` | `2`, `4`, `3600` | потоки `worker-telegram` (прод-compose), параллельные куски, предел FloodWait |
| `UPLOAD_DOWNLOAD_CONCURRENCY`, `UPLOAD_PREPARE_CONCURRENCY` | `4`, `4` | потоки воркеров (только прод-compose) |
| `UPLOAD_TMP_DIR` | `data/tmp` (compose — `/data/tmp`) | общий том тел загрузок и архивов |
| `UPLOAD_MAX_ACTIVE_TASKS`, `UPLOAD_MAX_FILES_ON_DISK` | `0`, `0` (без лимита) | лимиты задач и файлов в `tmp` |
| `UPLOAD_MIN_FREE_DISK_MB` | `1024` | резерв диска, иначе `507` |
| `UPLOAD_TASK_TTL`, `UPLOAD_ARCHIVE_TTL` | 7 дней, 24 ч | жизнь задач и готовых архивов |
| `UPLOAD_ARCHIVE_MAX_ITEMS` | `1000` | выбранных элементов в архиве |
| `UPLOAD_ARCHIVE_MAX_FILES`, `UPLOAD_ARCHIVE_MAX_SIZE_MB` | `10000`, `10240` (0 — без лимита) | файлов и МиБ в одном архиве (`422 too_many_items`, `413 too_large`) |
| `UPLOAD_CHUNK_SIZE`, `UPLOAD_SESSION_TTL` | 64 МиБ, 24 ч | кусок и наибольший срок сессии загрузки частями (≤ `client_max_body_size` прокси; TTL ≤ `UPLOAD_LOCK_TTL`) |
| `UPLOAD_SESSION_IDLE_TTL` | 1 ч | сессия без новых кусков истекает |
| `UPLOAD_USER_MAX_SESSIONS`, `UPLOAD_USER_DISK_QUOTA_MB` | `10`, `51200` (0 — без лимита) | квоты пользователя (кроме root): открытые сессии и МиБ временных файлов (`429 too_many_upload_sessions`, `507 temp_quota_exceeded`) |
| `LOG_LEVEL` | `INFO` | loguru |

Полный список с остальными интервалами (`UPLOAD_HEARTBEAT_INTERVAL`, `UPLOAD_SWEEP_INTERVAL`, `UPLOAD_RETRIES`, …) —
`src/config/models/upload.py` и соседние файлы.
