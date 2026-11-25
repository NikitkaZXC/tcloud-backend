# Testing

| Command | What |
|---|---|
| `make test` | unit (`tests/unit`): paths, Range, media, SSRF, the ClamAV protocol, disk budget, parallel chunk upload, settings |
| `make test-integration` | `tests/integration`: migrations up and down and `alembic check`, DB-level races, `TaskStore` and the Lua scripts for claim, priority change and cancel on Redis, `TaskSweeper`, queue priority on a real RabbitMQ, end-to-end worker tests in separate processes |
| `make test-api` | `tests/api`: HTTP through `httpx.AsyncClient` + `ASGITransport`, the app lifespan and real Celery workers in threads (broker `memory://`) with fake Telegram and ClamAV |
| `make test-all` / `make test-cov` | everything / with coverage |
| `make test-docker` | everything in a container with ffmpeg and Postgres (`docker-compose.test.yml`), the same as in CI |

- If `uv run pytest` fails because of stale paths in `.venv`, run `.venv/bin/python -m pytest` or recreate
  the environment (`rm -rf .venv && uv sync`).
- The `unit`/`integration`/`api` markers are set by folder automatically; `ffmpeg` marks tests that need ffmpeg,
  they are skipped without it.
- Postgres: testcontainers (`postgres:18`) or an existing server through `TEST_POSTGRES_HOST`/`TEST_POSTGRES_PORT`
  (then a temporary database of its own is created). Tables are cleared before every test.
- Redis: testcontainers (`redis:8`) or `TEST_REDIS_HOST`; `FLUSHDB` before every test.
  RabbitMQ: testcontainers (`rabbitmq:4-alpine`) or `TEST_RABBITMQ_URL`, only for the priority test.
- Workers (`celery.contrib.testing.worker.start_worker`, pool `threads`) start once per session: download and prepare
  with 4 threads each, telegram with 2. The test worker does not send `worker_ready`, so the fixture starts the heartbeat itself.
  Every test gets its own `WorkerRuntime` with fakes; after the test we wait until active tasks finish.
- `tests/api/test_tasks.py`: cancel in the queue and during sending (the message is deleted), priority change, URL retry,
  redelivery after a worker crash (after sending and after the commit, without a second message), a duplicate message
  and a stale generation are skipped, a webhook on cancel from the queue.
- `tests/api/test_settings.py`: user settings: defaults, partial `PATCH`, errors (unknown
  key, bad value, `unsafe_url`), reset, user isolation, API key, defaults applied to uploads (from a device,
  from a URL, in a batch, `encrypt` + `media`, webhook) and archives; `tests/unit/test_settings.py`: `with_defaults`, `merged`,
  `check_webhook`.
- API tests run on the `memory://` broker: it ignores priority, and several embedded workers on RabbitMQ in one
  process share the kombu event loop and corrupt AMQP frames (`UnexpectedFrame`). So ordering and crashes are checked
  separately, in `tests/integration/test_workers_e2e.py`:
  real `celery worker` processes (`tests/integration/worker_app.py`: the fake Telegram writes sends
  to a shared file) on RabbitMQ, Redis and Postgres from testcontainers:
  - `high` overtakes five waiting `low`; a raised priority of a waiting task moves it forward;
  - a worker killed with `SIGKILL` during sending: a second worker gets the task from RabbitMQ and sends once;
  - a worker killed after sending but before the `files` write: the second worker does not send again but writes the row
    with the same message_id.
  Verified by mutation: without `sent` in the task the test fails with two messages, without `x-max-priority` the ordering tests fail.
- Races and resource protection from the audit: `tests/api/test_replace_races.py` (`replace` after the target was deleted, a message gone
  during a parallel replace, old messages are deleted after the commit and on redelivery; the send is paused through
  `FakeTelegram.sent_hook` and `threading.Event`), `tests/api/test_quota.py` (parallel `POST /api/uploads`, files in
  the queue, a finished session and archives in the quota), `tests/api/test_auth_tokens.py` (JWT branches: expired, no `exp`, `none`,
  HS256, wrong `token_type`, deleted user; blocked by key, cookie and refresh gives `403`),
  `tests/unit/test_webhook.py` (one webhook in flight, retry with the fresh state), `tests/unit/test_telegram_client.py`
  (`TelegramClient` and `TelegramLogin` branches on a fake Telethon), `tests/unit/test_ci.py` (workflow token permissions and SHA pins).
- `tests/fakes.py`: `FakeTelegram` (channels and messages in memory, delay, FloodWait, network failures, a counter of
  concurrent sends) and `FakeScanner`. `tests/factories.py`: media samples (Pillow, ffmpeg lavfi).
- Races: 100 parallel uploads with 2 worker-telegram threads, waiting in the `uploads.telegram` queue, the FloodWait pause,
  the path lock in Redis, partial task updates do not overwrite each other.

## Load

```bash
make load-up        # Postgres, Redis and RabbitMQ for the stand + migrations
make load-app       # API on :8100 with a fake Telegram
make load-workers   # three Celery workers in separate processes on a real RabbitMQ (LOAD_TELEGRAM_DELAY, s per file)
make load           # locust headless: LOAD_CLIENTS, LOAD_SPAWN_RATE, LOAD_DURATION; report in logs/load/
make load-down
```

Thresholds: `LOAD_MAX_P95_MS` (500), `LOAD_MAX_FAIL_RATIO` (1%).
Every virtual user signs in as the stand's root (`ROOT_LOGIN=load` in `tests/load/env.load`) and issues itself
an API key, so the run is not bound by the 15-minute access token lifetime. `make load-up` creates JWT keys in `logs/load/`.
Setup requests (`setup: ...`: sign-in, key, storage) are not counted against the thresholds: 30 concurrent sign-ins are bound by argon2
(p95 ~3.5 s), which is the cost of password hashing, not of the working load.
`429` from the limiter is expected behaviour, the stand client waits for `retry_after`.
Measured on 2026-11-16, RabbitMQ and workers in separate processes, 100 users / 30 s: ~450 RPS, 0% errors,
`POST /api/uploads` p95 190 ms; the full upload path is bound by Telegram throughput
(2 threads × 0.2 s per file), the rest wait in the queue.
