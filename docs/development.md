# Development

Python 3.14, dependencies via `uv` (`pyproject.toml`, `uv.lock`). Groups: `test` (pytest, testcontainers, pip-audit),
`dev` = `test` + black, isort, locust (installed by default). Add a package with `uv add` / `uv add --group test|dev`.

```bash
make install        # uv sync
make env            # .env from .env.exemple + generated APP_SECRET_KEY, ROOT_PASSWORD, Postgres, RabbitMQ, Redis passwords and JWT keys in certs/
make dev-up         # Postgres, RabbitMQ (dashboard :15672), Redis on localhost
docker compose -f docker-compose.dev.yml up -d clamav   # ClamAV on :3310 (make dev-up does not start it)
make app            # uvicorn with --reload on 127.0.0.1:8000 (in .env: POSTGRES_HOST, CLAMAV_HOST, RABBITMQ_HOST, REDIS_HOST=127.0.0.1)
make worker-download  # Celery worker for the uploads.download queue
make worker-prepare   # Celery worker for the uploads.prepare queue
make worker-telegram  # Celery worker for the uploads.telegram and archives.build queues
make flower           # queue dashboard on :5555
make format         # black + isort
make check          # pip-audit + migration checks
uv run alembic revision --autogenerate -m "msg"
```

CI (`.github/workflows/ci.yml`, on push to `main` and on pull requests; `concurrency` cancels the previous run of the same branch): `lint` (black/isort `--check`), `test` (the whole `pytest` with Postgres, Redis, RabbitMQ and ffmpeg), `audit` (pip-audit). Dependabot (`.github/dependabot.yml`) updates uv, Docker, docker-compose and GitHub Actions once a month: all updates of an ecosystem (major, minor, patch) in one group, so one PR per ecosystem, at most 2 open.

Release (`.github/workflows/release.yml`, on push of a `v*.*.*` tag): builds the `Dockerfile` and publishes the image to
`ghcr.io/nikitkazxc/tcloud-backend` with the tags `{version}`, `{major}.{minor}` and `latest` and OCI labels (source, MIT,
description); the token additionally gets `packages: write`. Cutting a version: bump `version` in `pyproject.toml` (and `uv.lock`) and
the default `APP_VERSION` (`src/config/models/app.py`), extend `CHANGELOG.md`, then `git tag vX.Y.Z && git push --tags`.

The workflow token is read-only (`permissions: contents: read`). Actions are pinned by commit SHA, not by tag (a tag can
be moved); there are no comments in the YAML, the mapping lives here. Dependabot updates the SHAs itself; on a manual version change the SHA
comes from `git ls-remote --tags https://github.com/{owner}/{repo} 'refs/tags/{tag}*'` (for an annotated tag it is the
`^{}` line, that is the commit) or `gh api repos/{owner}/{repo}/git/ref/tags/{tag}` (for `object.type = tag` also
`gh api repos/{owner}/{repo}/git/tags/{sha}`), and the table below is updated in the same commit.

| Action | Tag | SHA |
|---|---|---|
| `actions/checkout` | `v7` (= `v7.0.1`) | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `astral-sh/setup-uv` | `v10.4.0` | `1db35dd8eee11c5894872e0b383ae6c406cf738d` |
| `docker/login-action` | `v4` (= `v4.8.0`) | `8aa5831fb8d48d8f76594c7ddde61ae58019f550` |
| `docker/metadata-action` | `v6` (= `v6.2.0`) | `dc802804100637a589fabce1cb79ff13a1411302` |
| `docker/build-push-action` | `v7` (= `v7.4.0`) | `c3c9e263c25d99ce0380d002d59b67737d91b0dc` |

Locally without ffmpeg the tests that need it are skipped; the full run is `make test-docker`.

## What to run

| Task | Commands | Where the code runs |
|---|---|---|
| Use the service | `make up` (and `make logs`) | everything in Docker, `docker-compose.prod.yml` |
| Edit code with auto-reload | `make dev-up`, then `make app` and the workers `make worker-*` | locally, only Postgres, RabbitMQ, Redis in Docker |
| Everything in Docker from sources | `make dev-up-all` | `docker-compose.dev.yml`, profile `app`: API, three workers and Flower on `127.0.0.1:5555` (basic auth `FLOWER_USER` / `FLOWER_PASSWORD` from `.env`, `tcloud` / `tcloud` without them) |

After any of them: sign in as root (`POST /api/auth/signin`, flows/users.md) and sign in to Telegram at `/docs`, section **Telegram** (flows/auth.md; with `make up` Swagger is disabled, use the frontend `/telegram` page, deployment.md). Locally sessions and temp
files live in `./data/` (default paths `data/session/...`, `data/tmp`; in Docker compose sets `/data/...`).
For `make app` the hosts in `.env` must be `127.0.0.1` (`POSTGRES_HOST`, `CLAMAV_HOST`, `RABBITMQ_HOST`, `REDIS_HOST`),
compose sets its own.

## Migrations

Alembic, `src/infrastructure/database/migrations/` (the revision list is in database.md). In Docker they are applied by
`scripts/docker-entrypoint.sh` when `app` starts (workers have `RUN_MIGRATIONS=0`), locally with `uv run alembic upgrade head`.
New revision: `uv run alembic revision --autogenerate -m "msg"`; existing ones are never edited.

## Script example

API key: `POST /api/users/{id}/api-keys` (flows/users.md).

```bash
curl -H "X-API-Key: $TCLOUD_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"Photos"}' http://127.0.0.1:8000/api/storages
curl -H "X-API-Key: $TCLOUD_KEY" --data-binary @photo.jpg \
  "http://127.0.0.1:8000/api/uploads?path=tcloud/<uuid>/vacation/photo.jpg&priority=high"
```

## Settings

`src/config/models/*`, one class per prefix: `APP_`, `POSTGRES_`, `JWT_`, `ROOT_`, `RATE_LIMIT_`, `TELEGRAM_`, `CLAMAV_`, `UPLOAD_`, `RABBITMQ_`, `REDIS_`, `LOG_`.
Read from `.env` (the path is changed with the `ENV_FILE` variable, empty means do not read, as in tests), environment variables win.
The template is `.env.exemple`, `make env` fills in the secrets. Validation errors do not print the given values
(`hide_input_in_errors`), so secrets do not reach the startup log. The main ones:

| Variable | Default | What |
|---|---|---|
| `APP_SECRET_KEY` | none (required, ≥ 32 characters, `make env`) | HMAC of refresh tokens and API keys, HKDF derives the users' webhook signing keys from it; shorter means settings do not load |
| `APP_ENCRYPTION_KEY` | empty: encryption off | 32 bytes base64, the file encryption key; losing it loses the encrypted files |
| `APP_MAX_BODY_SIZE` | `1048576` (1 MiB) | request body cap except streaming uploads (`413 body_too_large`, security.md) |
| `APP_ALLOWED_ORIGINS` | `[]` | CORS and the `Origin` check for a frontend on another origin |
| `ROOT_LOGIN`, `ROOT_PASSWORD` | none | root, synced on every API start; `ROOT_LOGIN` 1–64 characters (like the sign-in `login` field), `ROOT_PASSWORD` ≥ 12 characters, otherwise settings do not load |
| `JWT_SECURE` | `true` | cookies over HTTPS only; `false` for http without TLS |
| `JWT_PRIVATE_KEY_PATH`, `JWT_PUBLIC_KEY_PATH` | `certs/jwt-*.pem` | RS256 keys |
| `JWT_ACCESS_TOKEN_EXPIRATION_MINUTES` / `JWT_REFRESH_TOKEN_EXPIRATION_DAYS` | `15` / `30` | token lifetimes |
| `JWT_MAX_FAILED_LOGINS`, `JWT_LOCK_MINUTES` | `5`, `15` | account lockout after wrong passwords |
| `RATE_LIMIT_ENABLED` | `true` | rate limiting (`429 rate_limited` + `Retry-After`), docs/security.md |
| `RATE_LIMIT_<RULE>__LIMIT`, `RATE_LIMIT_<RULE>__WINDOW` | see docs/security.md | a rule as a pair; `JWT_SIGNIN_IP_LIMIT`/`JWT_SIGNIN_IP_WINDOW` were renamed to `RATE_LIMIT_SIGNIN_IP__LIMIT`/`__WINDOW` |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | IP/subnet of the reverse proxy that uvicorn trusts for `X-Forwarded-For`; never `*` |
| `POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | `tcloud` for the DB | PostgreSQL |
| `POSTGRES_POOL_RECYCLE` | `1800` | after how many seconds the API pool recreates a connection (`POSTGRES_POOL_PRE_PING` was removed, docs/architecture.md) |
| `RABBITMQ_HOST`, `RABBITMQ_USER`, `RABBITMQ_PASSWORD` | `rabbitmq`, `tcloud` | broker |
| `REDIS_HOST`, `REDIS_PASSWORD`, `REDIS_PREFIX` | `redis`, empty, `tcloud` | Redis ([docs/redis.md](docs/redis.md)) |
| `REDIS_CACHE_TTL` | `180` | lifetime of the Telegram data caches in Redis, s (channels, account, avatars, previews; docs/redis.md) |
| `REDIS_THUMB_CACHE_MB` | `64` | memory cap of the plaintext file preview cache in Redis (base64), `0` disables the cache |
| `CLAMAV_HOST`, `CLAMAV_PORT`, `CLAMAV_TIMEOUT` | `clamav`, `3310`, `900` | antivirus |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | none | the app on my.telegram.org |
| `TELEGRAM_SESSION`, `TELEGRAM_UPLOAD_SESSION`, `TELEGRAM_DOWNLOAD_SESSION` | `data/session/tcloud*` (compose: `/data/session/...`) | session files of `api`, `upload`, `download` |
| `TELEGRAM_CHATS` | empty | existing channels connected as storages at startup |
| `TELEGRAM_CONCURRENCY`, `TELEGRAM_UPLOAD_WORKERS`, `TELEGRAM_FLOOD_MAX_WAIT` | `2`, `4`, `3600` | `worker-telegram` threads (prod compose), parallel chunks, FloodWait limit |
| `UPLOAD_DOWNLOAD_CONCURRENCY`, `UPLOAD_PREPARE_CONCURRENCY` | `4`, `4` | worker threads (prod compose only) |
| `UPLOAD_TMP_DIR` | `data/tmp` (compose: `/data/tmp`) | shared volume for upload bodies and archives |
| `UPLOAD_MAX_ACTIVE_TASKS`, `UPLOAD_MAX_FILES_ON_DISK` | `0`, `0` (unlimited) | limits on tasks and files in `tmp` |
| `UPLOAD_MIN_FREE_DISK_MB` | `1024` | disk reserve, otherwise `507` |
| `UPLOAD_TASK_TTL`, `UPLOAD_ARCHIVE_TTL` | 7 days, 24 h | lifetime of tasks and ready archives |
| `UPLOAD_ARCHIVE_MAX_ITEMS` | `1000` | selected items in an archive |
| `UPLOAD_ARCHIVE_MAX_FILES`, `UPLOAD_ARCHIVE_MAX_SIZE_MB` | `10000`, `10240` (0 = unlimited) | files and MiB in one archive (`422 too_many_items`, `413 too_large`) |
| `UPLOAD_CHUNK_SIZE`, `UPLOAD_SESSION_TTL` | 64 MiB, 24 h | chunk size and the longest lifetime of a chunked upload session (≤ the proxy `client_max_body_size`; TTL ≤ `UPLOAD_LOCK_TTL`) |
| `UPLOAD_SESSION_IDLE_TTL` | 1 h | a session without new chunks expires |
| `UPLOAD_USER_MAX_SESSIONS`, `UPLOAD_USER_DISK_QUOTA_MB` | `10`, `51200` (0 = unlimited) | per-user quotas (except root): open sessions and MiB of temp files (`429 too_many_upload_sessions`, `507 temp_quota_exceeded`) |
| `LOG_LEVEL` | `INFO` | loguru |

The full list with the remaining intervals (`UPLOAD_HEARTBEAT_INTERVAL`, `UPLOAD_SWEEP_INTERVAL`, `UPLOAD_RETRIES`, …) is in
`src/config/models/upload.py` and the neighbouring files.
