# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project

tcloud: a FastAPI service that stores media (photo, video, audio, GIF) in private Telegram channels ("storages") through a Telegram **user** account (Telethon). Every file has a UUID and a path `tcloud/{storage uuid}/{folders}/{name}`; PostgreSQL keeps the index. Python 3.14, managed with `uv`. Structure and rules mirror `~/Desktop/nextgate/nextgate-backend`. The original spec is `docs/TZ.md`; detailed docs (Russian) are in `docs/` (index `docs/README.md`).

## Commands

```bash
make install / make env / make login / make up / make logs
make app                          # local uvicorn --reload (needs make dev-up)
make test | test-integration | test-api | test-all | test-cov
make test-docker                  # whole suite in Docker with ffmpeg (tests needing ffmpeg are skipped locally without it)
make format                       # black + isort
make check                        # pip-audit + migration checks
make load-up && make load-app && make load && make load-down   # locust stand with fake Telegram
uv run alembic revision --autogenerate -m "msg"
```

## Architecture

Layers: `api → application → infrastructure`; `utilities` are shared. `src/application` never imports `src/api`; `src/infrastructure` imports neither.

- `src/api/`: routers (`routes/`), HTTP schemas, `exception/` (error code → HTTP status table, handlers, body `{"error": {"code", "message", ...}}`), `dependencies/verify_api_key`. All routes are under `/api`, aggregated in `endpoints.py` with the API key check.
- `src/application/services/`: one class per domain (`AccountService`, `HealthService`, `StorageService`, `FolderService`, `FileService`, `TaskService`, `UploadService`) plus `UploadPipeline` (background processing). Services take an `AsyncSession` and `BackgroundRuntime`, build repositories in `__init__`, **own the commit**. Routes get services only via `src/application/factory/get_service_*`.
- `src/application/schemas/`: service DTOs; `src/application/exception/`: domain errors; `bootstrap/`: `login`, `startup`.
- `src/infrastructure/`: `database/` (models end with `DB`, mixins give UUIDv7 `id`/`created_at`/`updated_at`, repositories only `flush()`, migrations), `integrations/` (`telegram`, `antivirus`, `web`), `media/` (type sniffing, Pillow/ffprobe, thumbnails), `background/` (`Gate`, `DiskBudget`, `BackgroundRuntime`), `enums/`, `logger/`, `limits.py` (all Telegram limits).
- `src/utilities/`: `errors.TCloudError` (base error with `code`), `parsers/` (paths, Range), `formatters/` (constrained request types).
- Errors: every error subclasses `TCloudError` with a stable `code`; only the API layer maps codes to HTTP statuses. Telethon exceptions never leave `integrations/telegram` (`translate_errors`).
- Background state (stage gates, live progress, FloodWait pause, Premium cache) lives in `BackgroundRuntime` in `app.state`: **run a single uvicorn process**.

## Gotchas

- Telethon picks photo vs document by file extension: temp files keep the extension of the target name.
- Reads after ORM `UPDATE` with `onupdate=func.now()` need `populate_existing=True`, otherwise async lazy load raises `MissingGreenlet`.
- `media/analyze.py` is not called `probe.py` on purpose: the package exports a `probe` function.
- ClamAV image must be `*-debian` (alpine tags are amd64 only); stream limits are raised in `docker/clamd.conf`.
- Tests: `tests/conftest.py` sets `TEST_ENV` (with `ENV_FILE=""`) before importing `src`; API tests patch `main.create_telegram`/`main.create_scanner` with `tests/fakes.py`.

## Rules

- Plan first, then implement by the plan. Important decisions go to the owner as multiple-choice questions.
- Tests for every new feature (the more the better). Keep `docs/`, `README.md` and this file current in the same change; schema changes → `docs/database.md`.
- Every new query with a filter or sort gets an index. All our ids are UUID. Never edit existing migrations.
- Respect Telegram limits (`src/infrastructure/limits.py`, `docs/integrations.md`).
- No code comments or docstrings (one-line docstrings on exception classes only); explanations go to `docs/`.
- Log with loguru; never log secrets.
- Commits: one-line English messages (`feat:`, `fix:`, `refactor:`), one per logical change. **Never push.**
