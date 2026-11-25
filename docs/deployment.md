# Deployment

`docker-compose.prod.yml`: `app` (API), `worker-download`, `worker-prepare`, `worker-telegram`, `flower`, `postgres:18`,
`rabbitmq:4`, `redis:8`, `clamav`.
One image for the API and the workers: `python:3.14-slim` + ffmpeg + uv, runtime dependencies only, user `appuser`.
`scripts/docker-entrypoint.sh` applies migrations (workers have `RUN_MIGRATIONS=0`) and runs the service command.

```bash
make env            # fill in TELEGRAM_API_ID / TELEGRAM_API_HASH in .env; RabbitMQ, Redis and Flower passwords are generated
make up             # build and start
make logs
```

A ready image from GHCR instead of a build (published on every `v*.*.*` tag, docs/development.md): set
`TCLOUD_IMAGE` (in the `.env` next to the compose file or in the environment), then `pull` and `up` without building:

```bash
export TCLOUD_IMAGE=ghcr.io/nikitkazxc/tcloud-backend:1.0.0
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d --no-build
```

Without `TCLOUD_IMAGE` compose builds the local image `tcloud:latest` (`make up`). There is no frontend in this compose: the
`ghcr.io/nikitkazxc/tcloud-backend` image is only the API and the workers.

Before the first start set a non-obvious `ROOT_LOGIN` in `.env` (not `root`): after 5 wrong passwords in a row, sign-in to
the account is closed for 15 min for everyone, and with a known login root can be kept locked (security.md,
"Access"). `ROOT_PASSWORD` must be at least 12 characters, `make env` generates 24.

Then sign in as root (`ROOT_LOGIN`/`ROOT_PASSWORD`) on the frontend and sign in to Telegram on its `/telegram` page,
separately for the `api`, `upload` and `download` sessions (flows/auth.md). Session files live in the `session` volume and survive rebuilds; you only need to sign in again if
the volume was deleted or the session was terminated in Telegram. Without sign-in the app works, but Telegram requests answer
`503 telegram_unauthorized`.

- Swagger, ReDoc and `/openapi.json` are disabled in prod: compose passes `app` empty `APP_DOCS_URL`, `APP_REDOC_URL`,
  `APP_OPENAPI_URL` (an empty value means no route, `404`). To sign in to Telegram through Swagger temporarily without
  the frontend, set `APP_DOCS_URL=/docs` and `APP_OPENAPI_URL=/openapi.json` in `.env`, run
  `docker compose -f docker-compose.prod.yml up -d app` and open `http://127.0.0.1:8000/docs` on the server or through
  `ssh -L 8000:127.0.0.1:8000`, without publishing it through the proxy; then remove the variables and recreate `app` the same way.
- **`FORWARDED_ALLOW_IPS`** (in `.env`, compose passes it to the API, default `127.0.0.1`): the addresses from which uvicorn
  trusts `X-Forwarded-For`. If the reverse proxy is in another container (the frontend nginx), set the IP or subnet of its
  docker network (`docker network inspect …`, for example `172.18.0.0/16`), **never `*`**. Otherwise the API sees one proxy IP for all
  clients: the IP limits (`SIGNIN_IP`, `REFRESH_IP`, `HEALTH_IP`) are shared by everyone, and one password brute force
  locks everyone out of sign-in (security.md, "Rate limiting").
- The port is `127.0.0.1:8000`. For outside access use a reverse proxy with TLS: the frontend on `site.com`, only `/api/*` to the backend, keeping `Host` (otherwise the `Origin` check rejects requests, see security.md).
- `make env` puts the RS256 JWT keys into `certs/` (they never get into git or the image), compose mounts them into `/certs` read-only.
  Cookies are `Secure` (`JWT_SECURE=true`): TLS is required outside. The app in the container is uid 1000: on Linux, if you are not uid 1000,
  run `sudo chown 1000 certs/jwt-private.pem`, otherwise the API does not start (`JWT key ... is not readable`).
- `worker-telegram` is exactly one replica (a shared FloodWait pause and one session), it listens on `uploads.telegram` and
  `archives.build` (folder archive builds); parallelism is
  `TELEGRAM_CONCURRENCY` and `TELEGRAM_UPLOAD_WORKERS`. `worker-download` and `worker-prepare` can be scaled
  with replicas (`docker compose up --scale worker-prepare=2`), threads per replica are `UPLOAD_DOWNLOAD_CONCURRENCY` and
  `UPLOAD_PREPARE_CONCURRENCY`.
- Worker memory is capped: `mem_limit: ${WORKER_MEM_LIMIT:-2g}` on `worker-download`, `worker-prepare` and
  `worker-telegram` (a variable in `.env` or the compose environment, Docker format: `1g`, `3g`). A file bomb for Pillow,
  ffprobe or an archive build kills only the worker container (OOM), not the whole host; after the worker restarts the task
  is retried or marked lost (background-tasks.md, "Failures"). With many threads
  (`*_CONCURRENCY`) raise the limit.
- Flower is `127.0.0.1:5555` with basic auth (`FLOWER_USER` / `FLOWER_PASSWORD`); outside only through a reverse proxy with TLS.
  Flower gets neither `.env` nor volumes: compose passes it only `RABBITMQ_USER`, `RABBITMQ_PASSWORD`,
  `FLOWER_USER`, `FLOWER_PASSWORD`, and it runs as `celery --broker=amqp://… flower` without `--app`: the tcloud
  app (and its settings with `APP_*`, `POSTGRES_*`, `TELEGRAM_*`) is not imported. Workers, tasks and `inspect` are visible
  through events and pidbox; task names come in the events. The RabbitMQ password must be URL-safe (`make env` generates
  such a one), the vhost is `/`.
- `GET /api/health` when signed in (cookie or `X-API-Key`) shows the full report: DB, broker, live workers per queue, session authorization
  (`telegram`, `telegram_upload`, `telegram_download`), ClamAV, Redis, the overall `ok` and `details` (the time of each check `latency_ms`, the age of worker heartbeats `last_seen_s`, background-tasks.md); the answer is always `200`. Without sign-in only
  `{"status": "ok"}` with `200` or `{"status": "degraded"}` with `503` (the same `ok`, no details), suitable for external
  monitoring and a load balancer. Wrong credentials → `401`, as on the other endpoints.
- The `tmp` volume is shared by the API and the workers and must fit the files waiting in the queue (the size is unlimited unless root set `uploads.max_file_size`; with encryption ≈ 2× the file size).
- RabbitMQ: `consumer_timeout` is raised to 24 h, otherwise the broker closes the channel during a long upload (30 min by default).
  Queues are durable, messages persistent; Redis runs with `appendonly yes`, so the queue and statuses survive a restart.
- A network drop between a worker and the broker (tested up to 3 min) does not kill the process: Celery reconnects by itself (the pidbox queue
  is not exclusive, background-tasks.md). The safety net is `restart: unless-stopped` on the API and workers in the prod and dev compose.
- Container logs (prod and dev compose): the `json-file` driver with rotation `max-size: 50m`, `max-file: "5"`, up to
  250 MB per container (the `x-logging` anchor), otherwise Docker logs grow without limit and fill the disk.
- The first ClamAV start downloads the databases for a few minutes; until then uploads answer `503 scanner_unavailable`.
- ClamAV (`docker/clamd.conf`, integrations.md): `MaxFileSize 2147483645` (the libclamav ceiling), `StreamMaxLength`/
  `MaxScanSize 4000M`, `MaxScanTime` 30 min; files bigger than `CLAMAV_MAX_FILE_SIZE` are not scanned (`too_big_to_scan`).
  When changing `MaxFileSize`, change `CLAMAV_MAX_FILE_SIZE` too. Memory: the signature databases take ≈ 1–1.5 GB RAM, during a database update
  (`ConcurrentDatabaseReload` by default) clamd briefly holds two copies, so plan 3–4 GB RAM for the container.
  `INSTREAM` writes the stream to a clamd temp file on disk (not to memory): every concurrent check needs up to
  2 GiB of space in the container plus the unpacked archive contents (up to `MaxScanSize`). There are never more concurrent checks than
  `worker-prepare` threads (`UPLOAD_PREPARE_CONCURRENCY` × replicas). Checking 2 GiB takes minutes; the client waits for the answer
  up to `CLAMAV_TIMEOUT` (1860 s).
- The Postgres password is set when the `pgdata` volume is first created; changing it in `.env` later means recreating the volume.
- Request body size at the proxy: a plain `POST /api/uploads` carries the whole file, so the frontend nginx has
  `client_max_body_size 0` and `proxy_request_buffering off` for `/api/` (the other endpoints are fine with 1 MiB: the API itself cuts bodies
  above `APP_MAX_BODY_SIZE`, security.md). If the proxy limits the body (your own nginx, a CDN with
  a ~100 MB limit), upload big files with chunked sessions (flows/uploads.md): the body of one `PUT` is at most
  `UPLOAD_CHUNK_SIZE` (64 MiB), so the proxy limit must be ≥ `UPLOAD_CHUNK_SIZE`, otherwise lower it.
- Reverse proxy in front of the API (the frontend nginx): file bodies must not be compressed; `GET /api/files/{id}/download` and
  `GET /api/tasks/{id}/archive` send `Cache-Control: no-transform` and an exact `Content-Length` (`Range`
  and browser progress rely on it). nginx ignores `no-transform`, and `gzip_proxied` only looks at requests with `Via`,
  so gzip for these paths is turned off by a separate `location` with the same proxying as `/api/`:

  ```nginx
  location ~ ^/api/(files/[^/]+/(download|thumb)|tasks/[^/]+/archive)$ {
      gzip off;
      proxy_pass ${API_UPSTREAM};
      # the same proxy_set_header / proxy_buffering off / timeouts as in location /api/
  }
  ```

  Documents already come as `application/octet-stream`, and media and archives are not `application/json`, so with
  `gzip_types application/json` none of them is compressed today; the `location` is a safety net in case
  `gzip_types` is extended.

## Backups and restore

Telegram holds only the file bodies. The index (paths, names, MIME, file keys) lives only in PostgreSQL, the keys only in
`.env`. The index cannot be rebuilt from the Telegram channels.

| What | How to back it up | What is lost without it |
|---|---|---|
| PostgreSQL | a regular `pg_dump` (below), an encrypted copy off the server | the whole index: the files stay in the channels, but without paths, names and wrapped keys they are effectively lost |
| `APP_ENCRYPTION_KEY` (`.env`) | an offline copy of `.env` (password manager, paper, a safe), separate from the DB dump | all encrypted files and their previews, forever: the file keys are wrapped by this key |
| `APP_SECRET_KEY` (`.env`) | the same place, with the same copy of `.env` | all API keys and refresh tokens (everyone signs in again, keys are issued again), every user's webhook signing key changes, so receivers must fetch the key again from `GET /api/account/webhook-secret` |
| `certs/` (JWT keys) | a copy together with `.env` | only user sessions: everyone signs in again |
| the `session` volume (Telegram sessions) | an **encrypted** archive (a session file is access to the Telegram account) | signing in to Telegram again for `api`, `upload`, `download` (flows/auth.md); no files are lost |
| Redis | not backed up | queued tasks and ready archives (`archives`): they are started again; statuses, caches and limits rebuild themselves |

The other `.env` secrets (Postgres, RabbitMQ, Redis, Flower passwords) can be restored by changing them, but a full copy of `.env`
is simpler. Keep the DB dump and `.env` apart: the dump holds the wrapped file keys, `.env` the key that opens them.

```bash
# dump (cron, for example once a day) and encryption
docker compose -f docker-compose.prod.yml exec -T postgres pg_dump -U tcloud -Fc tcloud \
  | gpg --symmetric --cipher-algo AES256 -o tcloud-$(date +%F).dump.gpg
# Telegram sessions
docker run --rm -v tcloud_session:/s:ro alpine tar czf - -C /s . \
  | gpg --symmetric --cipher-algo AES256 -o tcloud-session-$(date +%F).tgz.gpg
# DB restore (API and workers stopped)
gpg -d tcloud-2026-01-01.dump.gpg \
  | docker compose -f docker-compose.prod.yml exec -T postgres pg_restore -U tcloud -d tcloud --clean --if-exists
```

A restored dump does not know about files uploaded after it (they stay in the channels without DB rows), and files
deleted after it remain in the index without messages in Telegram. So dump often.

**Test restore**, right after setting up backups and regularly afterwards: start a separate `postgres:18`
(`docker run -d --name restore-test -e POSTGRES_PASSWORD=x postgres:18`), restore a fresh dump into it
(`pg_restore -U postgres -d postgres --no-owner`), check `SELECT count(*) FROM files` and
`SELECT max(created_at) FROM files` and delete the container. Compare the key from the offline copy of `.env` with the live one, for example by
the `sha256sum` of the `APP_ENCRYPTION_KEY` line. Treat an untested backup as missing.

- **There is no key rotation**: neither `APP_ENCRYPTION_KEY` nor `APP_SECRET_KEY` can be changed without the losses in the table above.
  If a key leaks, old files stay under it.
- **The `tmp` volume**: upload bodies before encryption and sending, and ready folder archives (until `UPLOAD_ARCHIVE_TTL`,
  24 h by default), sit there as plaintext. Keep the Docker volumes (`/var/lib/docker`) on an encrypted disk
  (LUKS/dm-crypt or cloud disk encryption); that also covers `session` and `pgdata`. `tmp` does not need a backup.
