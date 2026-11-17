# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project

tcloud: a FastAPI service that stores media (photo, video, audio, GIF) and other files (documents; executables are blocked) in private Telegram channels ("storages") through a Telegram **user** account (Telethon). Every file has a UUID and a path `tcloud/{storage uuid}/{folders}/{name}`; PostgreSQL keeps the index. Python 3.14, managed with `uv`. Structure and rules mirror `~/Desktop/nextgate/nextgate-backend`. Detailed docs (Russian) are in `docs/` (index `docs/README.md`).

## Commands

```bash
make install / make env / make up / make logs   # then sign in as root (/api/auth/signin) and log in to Telegram on /docs → Telegram (docs/flows/auth.md)
make app                          # local uvicorn --reload (needs make dev-up: Postgres, RabbitMQ, Redis)
make worker-download | worker-prepare | worker-telegram | flower   # local Celery workers and Flower
make test | test-integration | test-api | test-all | test-cov
make test-docker                  # whole suite in Docker with ffmpeg (tests needing ffmpeg are skipped locally without it)
make format                       # black + isort
make check                        # pip-audit + migration checks
make load-up && make load-app && make load && make load-down   # locust stand with fake Telegram
uv run alembic revision --autogenerate -m "msg"
```

## Architecture

Layers: `api → application → infrastructure`; `utilities` are shared. `src/application` never imports `src/api`; `src/infrastructure` imports neither.

- `src/api/`: routers (`routes/`), HTTP schemas, `middleware/` (security headers, `Origin` check against CSRF; CORS for `APP_ALLOWED_ORIGINS` with credentials in `main.py`), `exception/` (error code → HTTP status table, handlers, body `{"error": {"code", "message", ...}}`), `dependencies/get_actor` (access cookie or `X-API-Key` → `Actor`) and `get_root`. All routes are under `/api`, aggregated in `endpoints.py`; `/api/telegram` is root-only.
- `src/application/services/`: one class per domain (`AccountService`, `AuthService`, `HealthService`, `StorageService`, `FolderService`, `FileService`, `TaskService`, `UploadService`, `UserAuthService`, `UserService`, `GrantService`, `ApiKeyService`) plus `UploadPipeline` (stages run by Celery workers). Services take an `AsyncSession` and `AppRuntime`, build repositories in `__init__`, **own the commit**. Routes get services only via `src/application/factory/get_service_*`.
- `src/application/schemas/`: service DTOs; `src/application/exception/`: domain errors; `bootstrap/`: `startup`.
- `src/infrastructure/`: `database/` (models end with `DB`, mixins give UUIDv7 `id`/`created_at`/`updated_at`, repositories only `flush()`, migrations), `integrations/` (`telegram`, `antivirus`, `web`), `media/` (type sniffing, Pillow/ffprobe, thumbnails), `background/` (Celery app with priority queues `uploads.download`/`uploads.prepare`/`uploads.telegram`/`archives.build`, `enqueue`, `queue_sizes`, `DiskBudget`, `AppRuntime`, `WorkerRuntime`, `Heartbeat`), `cache/` (`TaskStore` in Redis with Lua `CLAIM`/`REQUEUE`/`CANCEL`), `enums/`, `logger/`, `limits.py` (all Telegram limits).
- `src/utilities/`: `errors.TCloudError` (base error with `code`), `parsers/` (paths, Range), `formatters/` (constrained request types).
- Errors: every error subclasses `TCloudError` with a stable `code`; only the API layer maps codes to HTTP statuses. Telethon exceptions never leave `integrations/telegram` (`translate_errors`).
- Uploads: API writes the body to the shared `tmp` volume, task state lives in Redis (no tasks table), Celery on RabbitMQ runs `src/application/tasks`. Workers use `--pool threads`; `WorkerRuntime` runs one event loop per worker process with its own Telethon session (`TELEGRAM_UPLOAD_SESSION`). **Run one `worker-telegram` replica** (shared FloodWait pause). Reads from Telegram stay in the API on a separate client and session (`runtime.download`, `TELEGRAM_DOWNLOAD_SESSION`). Queue messages carry only `task_id` + `generation`; a worker must `claim` before work (stale generation, cancelled or duplicate messages are skipped). `sent`/`committed` in the task make Telegram sends idempotent on redelivery. `TaskSweeper` in the API fails tasks lost by the broker.

## Gotchas

- Telethon picks photo vs document by file extension: temp files keep the extension of the target name.
- Reads after ORM `UPDATE` with `onupdate=func.now()` need `populate_existing=True`, otherwise async lazy load raises `MissingGreenlet`.
- `media/analyze.py` is not called `probe.py` on purpose: the package exports a `probe` function.
- ClamAV image must be `*-debian` (alpine tags are amd64 only); stream limits are raised and alerts made strict in `docker/clamd.conf` (`test_clamd_config_is_strict` guards it). `Heuristics.Encrypted|Limits.Exceeded|OLE2.ContainsMacros` mean `unscannable`, every other hit is `infected`.
- Tests: `tests/conftest.py` sets `TEST_ENV` (with `ENV_FILE=""`) before importing `src`; API tests patch `main.create_telegram`/`main.create_login`/`main.create_scanner` with `tests/fakes.py` (the `upload_session` fixture creates the upload session file uploads require) and run real Celery workers in threads on `memory://` (session-scoped), swapping `src.application.tasks.upload.runtime` per test.
- Never run several embedded Celery workers on RabbitMQ in one process (shared kombu hub corrupts AMQP frames): API tests use `memory://`; ordering and crash tests spawn real `celery worker` processes (`tests/integration/test_workers_e2e.py`).
- Only the API Telegram client has `receive_updates=True` (manual deletions in Telegram drop DB rows via `events.MessageDeleted`); register the handler before `catch_up()`. Workers create clients with `receive_updates=False`.
- Users: roles `root` (single, synced from `ROOT_LOGIN`/`ROOT_PASSWORD` on every API start), `admin`, `user`; no self sign-up, login + password only (no email). JWT RS256 in HttpOnly cookies (`access`/`refresh`, keys in `certs/`), refresh rotation with reuse detection, revoked access `jti` in Redis. Per-user API keys in `X-API-Key`. Rights are read from the DB on every request, never from the token. Every service method that touches storages/folders/files/tasks takes an `Actor`; checks live in `services/access.py` (grant `recursive` = folder and below, otherwise only files directly in it; `can_read/can_upload/can_delete`). Tasks carry `user_id`; users see only their own (docs/flows/users.md).
- Telegram login is API-only and root-only (`/api/telegram/{api|upload|download}/qr|code|sign-in|password|logout`); the app starts without sessions and Telegram calls answer `503 telegram_unauthorized`. Login state lives in API process memory (one uvicorn process). `TelegramLogin` writes `login-*.session` and atomically replaces the session file on success. Never add a session that accepts foreign QR tokens. Default paths are local `data/...`; compose sets `/data/...`.
- Folder archives (`POST /api/folders/archive`, zip/7z/tar.gz/tar.bz2/tar.xz at max compression) are background tasks (`params.source = archive`, stage `archiving`) on queue `archives.build`, consumed by the single `worker-telegram` (upload session reads the files); the ready file stays in `UPLOAD_TMP_DIR` for `UPLOAD_ARCHIVE_TTL` (Redis zset `archives`, removed by `TaskSweeper.clean_tmp`) and is downloaded via `GET /api/tasks/{id}/archive` by the task owner only. `UPLOAD_MAX_ACTIVE_TASKS`/`UPLOAD_MAX_FILES_ON_DISK` = 0 means unlimited (default); only `UPLOAD_MIN_FREE_DISK_MB` guards the disk.
- Encryption (`encrypt=true`, only `send_as=file`): `infrastructure/security/encryption.py`, AES-256-GCM in 64 KiB blocks, per-file DEK wrapped by `APP_ENCRYPTION_KEY`; header in the TG file and `files.encryption_header`; `files.size` is the plaintext size, the TG size is `cipher_size(size)` (`services/file.stored_size`); download/archives decrypt via `services/file.file_body`. Losing the key loses the files.
- RabbitMQ `consumer_timeout` must stay raised (compose sets 24 h), otherwise long Telegram uploads lose their channel at 30 min.

## Rules

- Plan first, then implement by the plan. Important decisions go to the owner as multiple-choice questions.
- Tests for every new feature (the more the better). Keep `docs/`, `README.md` and this file current in the same change; schema changes → `docs/database.md`.
- Every new query with a filter or sort gets an index. All our ids are UUID. Never edit existing migrations.
- Respect Telegram limits (`src/infrastructure/limits.py`, `docs/integrations.md`).
- No code comments or docstrings (one-line docstrings on exception classes only); explanations go to `docs/`.
- Log with loguru; never log secrets.
- Commits: one-line English messages (`feat:`, `fix:`, `refactor:`), one per logical change. **Never push.**
