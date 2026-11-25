# Security

## Access

- Users with the roles `root`/`admin`/`user`, sign-in by login and password (argon2), JWT RS256 in HttpOnly cookies with refresh
  rotation and reuse detection, lockout after wrong passwords, per-user API keys in `X-API-Key`. Refresh tokens and
  keys are stored only as HMAC-SHA256 with `APP_SECRET_KEY`. Details in flows/users.md. The JWT keys (`certs/`)
  are read and parsed once per process (`security/jwt.py`, `_private_key`/`_public_key` are
  `cryptography` objects, not PEM strings): signing a token at sign-in and refresh takes ~0.8 ms instead of ~72 ms, verifying ~83 µs
  instead of ~100 µs. New keys are picked up by a restart.
- Rights are checked on every request against the DB (role, blocking, folder grants), not from the token contents.
- There is no sign-up; Telegram sessions (`/api/telegram`) are root-only.
- Sign-in is limited per IP and per login (rate limiting, below) on top of the account lockout.
- Account lockout: after `JWT_MAX_FAILED_LOGINS` (5) wrong passwords in a row, sign-in to that account is closed for
  `JWT_LOCK_MINUTES` (15) min (`429 account_locked`) from any IP, the owner's included. Cookies and
  API keys already issued keep working, an API restart does not lift the lock. That is why **in prod `ROOT_LOGIN` must be a non-obvious login,
  not `root`/`admin`**: anyone who knows the login can lock root again every 15 min with five attempts. To lift it earlier:
  change `ROOT_LOGIN` in `.env` and restart the API (root is renamed, the login is synced at startup) or run
  `UPDATE users SET locked_until = NULL WHERE role = 'root'`.
- The port is published only on `127.0.0.1`. Postgres and ClamAV are not exposed.

## Rate limiting

- Redis, sliding window (request log): one Lua script `RATE_LIMIT_SCRIPT` (`infrastructure/cache/limits.py`,
  `RateLimitStore`) keeps a sorted set per key with the Redis time (`TIME`) of every accepted request. Entries older than the window
  are removed, rejected requests are not recorded, `Retry-After` is when the oldest entry leaves the window. A key of another
  type (for example, an old counter) is replaced. Keys are `{REDIS_PREFIX}:rate_limit:{scope}:{ip|user}:{id}` (redis.md).
- In code (`src/api/dependencies/rate_limit.py`): the dependencies `rate_limit_by_ip(scope, rule)` /
  `rate_limit_by_user(scope, rule)` (the user is `actor.user_id`, so requests with `X-API-Key` count towards the
  key owner together with their cookies) or `RateLimiter.hit` for keys from the body (sign-in uses the lowercased login).
  `rate_limit_api` is the shared rule `API_USER`.
- Rules are `RateLimitRule(limit, window)` in `RateLimitSettings` (`src/config/models/rate_limit.py`), variables
  `RATE_LIMIT_<RULE>__LIMIT` and `RATE_LIMIT_<RULE>__WINDOW` (set as a pair: one without the other fails startup).
  Switch everything off: `RATE_LIMIT_ENABLED=false`.
- Exceeding a limit gives `429 rate_limited`, body `{"error": {"code": "rate_limited", "message": ..., "retry_after": N}}` and the
  header `Retry-After: N` (seconds, ≥ 1). If Redis is down, the request passes (fail open, logged as
  `Rate limit check failed`); the account lockout keeps working.
- Endpoint limits are set in the routes (or when the router is included in `endpoints.py`), values are `RATE_LIMIT_*`.

| Rule | Default | Key | Where |
|---|---|---|---|
| `SIGNIN_IP` | 20 / 60 s | IP | `POST /api/auth/signin` |
| `SIGNIN_LOGIN` | 10 / 900 s | login | `POST /api/auth/signin` (`RateLimiter.hit` from the body) |
| `REFRESH_IP` | 60 / 60 s | IP | `POST /api/auth/refresh`, `POST /api/auth/logout` (separate counters) |
| `HEALTH_IP` | 120 / 60 s | IP | `GET /api/health` |
| `TELEGRAM_LOGIN_USER` | 10 / 600 s | user | `POST /api/telegram/{session}/qr`, `/code`, `/sign-in`, `/password` |
| `UPLOAD_USER` | 300 / 60 s | user | `POST /api/uploads`, `/uploads/from-url`, `/uploads/sessions`, every `PUT /uploads/sessions/{id}` (shared counter; 429 before the body is read) |
| `UPLOAD_BATCH_USER` | 30 / 60 s | user | `POST /api/uploads/batch` |
| `TELEGRAM_SEND_MINUTE_USER` | 30 / 60 s | user (except root) | tasks that send to Telegram: checked at creation, recorded when queued, `429 send_quota_exceeded` (flows/uploads.md, "Telegram send quota") |
| `TELEGRAM_SEND_DAY_USER` | 2000 / 86400 s | user (except root) | the same, per day |
| `STORAGE_CREATE_USER` | 5 / 3600 s | user | `POST /api/storages` (each one is a new Telegram channel; Telegram bans frequent channel creation) |
| `ARCHIVE_USER` | 30 / 3600 s | user | `POST /api/folders/archive`, `/folders/archive/selection` |
| `PREVIEWS_BACKFILL_USER` | 3 / 3600 s | user | `POST /api/files/previews/backfill` |
| `PREVIEWS_CLEAR_USER` | 3 / 3600 s | user | `DELETE /api/files/previews` |
| `FILE_READ_USER` | 3000 / 60 s | user | `GET /api/files/{id}/download`, `/files/{id}/thumb`, `/tasks/{id}/archive`, instead of the shared rule, so that the grid of a big folder does not hit 429 |
| `API_USER` | 1200 / 60 s | user | every other signed-in endpoint (routers users, telegram, settings, storages, folders, uploads/tasks, files; `GET /api/account`; `/auth/logout/all`, `/auth/sessions*`, `/auth/password`), on top of their own rules |

The client IP is `request.client.host`. uvicorn takes it from `X-Forwarded-For` only from the addresses in
`FORWARDED_ALLOW_IPS` (`127.0.0.1` in the image). Behind the frontend nginx in another container set
`FORWARDED_ALLOW_IPS` in `.env` to the proxy IP or subnet, **never `*`**: otherwise a client forges its IP through `X-Forwarded-For`
and bypasses the IP limits. Until it is set, all clients behind the proxy share one IP (IP limits are shared by everyone).

## Files

Files are never executed and never opened by anything except validating decoders and ClamAV.
The honest limit: ClamAV catches the known and the typical, not everything. That is why whatever cannot be checked is rejected by default.

Media and any other files are accepted, including executables, scripts and installers (exe, dmg, sh, apk…):
there is no list of forbidden types. The protection against them is that non-media is never opened in a browser on our address
("Downloads") and ClamAV. The checks in order:

1. The type is detected by magic bytes (`filetype`); the extension and `Content-Type` are ignored.
2. Media is checked by a decoder: Pillow (`verify` + `load`, a pixel limit against decompression bombs),
   ffprobe in a subprocess without a shell, with a timeout and `-protocol_whitelist file` (the ffmpeg preview too, before `-i`):
   an HLS playlist or concat inside a file cannot make the decoder go to the network. Video/audio are told apart by streams, not by container.
3. The extension must match the real type (`extension_mismatch`): media only with a media extension,
   non-media cannot be called `.jpg`/`.mp4`/… (otherwise Telegram would show it as a photo or video).
4. ClamAV (`clamd INSTREAM`) with the strict `docker/clamd.conf`: `AlertExceedsMax` (without it a file that hits a limit
   passes unchecked), `AlertEncrypted`, `AlertOLE2Macros`, `AlertBrokenExecutables`, `DetectPUA`, `HeuristicAlerts`,
   `MaxRecursion 16`, `MaxFiles 10000`, `MaxScanTime` 30 min, `MaxFileSize` = the ClamAV ceiling of 2147483645 bytes,
   `MaxScanSize`/`StreamMaxLength 4000M`. A virus, PUA or broken executable → `infected`.
   Encrypted content, Office with macros, a content limit exceeded → `unscannable` (see integrations.md).
   A file bigger than `CLAMAV_MAX_FILE_SIZE` (= `MaxFileSize`) cannot be checked by ClamAV at all: it is not sent to clamd and
   is accepted with the warning `too_big_to_scan` even without `allow_unscannable` (the owner's decision; the honest limit is that
   such files pass only the type check and decoders). Downloading non-media stays sandboxed ("Downloads"). ClamAV down → the upload
   is rejected (fail-closed), after `UPLOAD_RETRIES` retries, since ClamAV may simply be restarting.
5. `allow_unscannable=true` on an upload accepts `unscannable` with a warning in the task. It does not let viruses through:
   a matched signature wins over heuristics (`HeuristicScanPrecedence no`).
6. Temp files: random names, mode `0600`, deleted on every outcome; the user's file name never reaches the file system,
   only its extension (Telethon decides photo vs document by it) and only if it is short and simple
   (`[a-z0-9]{1,16}` after lowercasing, `temp.SUFFIX`), otherwise a name without an extension: a long or
   unusual extension does not break the file name on disk.
7. Numbers from the request are bounded so they never cause a 500: at most 19 digits per bound in `Range` (otherwise `416 bad_range`),
   search `size_min`/`size_max` ≤ 2⁶³−1 (otherwise `422`).

## Request body size

FastAPI reads a JSON body in full before the sign-in and rate limit dependencies, so without a cap an anonymous request to
`/api/auth/signin` could push gigabytes into API memory. `BodyLimitMiddleware` (`src/api/middleware/body_limit.py`)
answers `413 body_too_large` (`{"error": {"code": "body_too_large", "message", "limit"}}`) before routing and sign-in:

- `Content-Length` above `APP_MAX_BODY_SIZE` (1 MiB): at once, the body is not read;
- a body without `Content-Length` (chunked): bytes are counted while reading, on overflow the same `413` is sent, and
  the app receives `http.disconnect` and reads or answers nothing more.

Exceptions are the endpoints that stream the body to disk with their own limits (`STREAMING`): `POST /api/uploads`
(`uploads.max_file_size`), `PUT /api/uploads/sessions/{id}` (`UPLOAD_CHUNK_SIZE`), `PUT /api/storages/{id}/photo`
(the Telegram photo limit). The test `test_only_body_streaming_routes_are_exempt` keeps this list equal to the streaming endpoints.
The biggest JSON body is `POST /api/folders/archive/selection` with `UPLOAD_ARCHIVE_MAX_ITEMS` long paths; if
thousands of folders with paths near 1024 characters are selected, raise `APP_MAX_BODY_SIZE`. The frontend nginx sets
`client_max_body_size 1m` for `/api/` separately, except for the same streaming endpoints.

## Temp files: quotas

Upload bodies sit on the shared `tmp` volume, and one user could fill it (many chunked sessions without
chunks, big `size` values). So, on top of the global `UPLOAD_MIN_FREE_DISK_MB`, every user except root has:

- `UPLOAD_USER_MAX_SESSIONS` (10) open chunked sessions → `429 too_many_upload_sessions`;
- `UPLOAD_USER_DISK_QUOTA_MB` (51200) of bytes in temp files (live sessions in full + the full size of their tasks until
  they finish, `disk:usage`, including ready archives until they expire) → `507 temp_quota_exceeded` (like `insufficient_storage`:
  space ran out, but the user's own); archives are checked too;
- a short life for an abandoned session: `UPLOAD_SESSION_IDLE_TTL` (1 h) without chunks, `UPLOAD_SESSION_TTL` (24 h) at most;
- root and admin cancel the sessions of the users they manage (`DELETE /api/uploads/sessions/{id}`), blocking and deleting
  a user cancels all their sessions and unfinished tasks.

Details in flows/uploads.md, "From a device in chunks".

## CSRF and headers

- `OriginCheckMiddleware`: POST/PUT/PATCH/DELETE with an `Origin` header whose host does not match the request `Host` and
  which is not in `APP_ALLOWED_ORIGINS` get `403 cross_site_request`. Requests without `Origin` (scripts, curl) pass.
  If the proxy changes `Host`, add the external address to `APP_ALLOWED_ORIGINS`. Together with `SameSite=lax` this closes
  CSRF on cookies.
- `SecurityHeadersMiddleware` on every response: `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  COOP/CORP, `Permissions-Policy`; CSP `default-src 'none'` and `Cache-Control: no-store` unless the route set its own
  (except the Swagger paths, `settings.app.docs_paths`, since Swagger would not load otherwise); HSTS with `JWT_SECURE=true`.

## CORS (frontend)

- Deployment layout: the frontend on `site.com`, the backend on `site.com/api`. That is one origin, the browser needs no CORS,
  `APP_ALLOWED_ORIGINS` is empty. The proxy sends `/api/*` to the backend and keeps `Host`. Swagger (`APP_DOCS_URL`,
  `APP_REDOC_URL`, `APP_OPENAPI_URL`, by default `/docs`, `/redoc`, `/openapi.json`) is disabled in the prod compose with empty
  values (empty means no route) and is not published through the proxy (deployment.md).
- `CORSMiddleware` (in `main.py`, the outermost one, as in nextgate) is configured by `APP_ALLOWED_ORIGINS`,
  `APP_IS_ALLOWED_CREDENTIALS`, `APP_ALLOWED_METHODS`, `APP_ALLOWED_HEADERS`. It is needed for the frontend dev server on another
  port (`["http://localhost:3000"]`). The list is empty by default: foreign browser JS cannot see responses.
  `OriginCheckMiddleware` lets the same list through, so the frontend address is added in one place.
- Headers exposed to the frontend (`APP_EXPOSED_HEADERS`): `Accept-Ranges`, `Content-Disposition`, `Content-Range`, `Retry-After`, `Upload-Offset`.
- The frontend must be on the same site (for example `app.example.com` and `api.example.com`): `SameSite=lax` cookies and
  `Cross-Origin-Resource-Policy: same-site` do not work across different sites. Another site would need `JWT_SAME_SITE=none`
  (only with `JWT_SECURE=true`) and a CORP change; that weakens protection and is a separate decision.
- fetch with cookies: `credentials: "include"`.

## Downloads

Download only: anything that is not a photo, video, audio or GIF reaches the browser as a file, and nothing is executed or
shown on the tcloud address.

- `GET /api/files/{uuid}/download` (both `200` and `206` with `Range`) always sends `X-Content-Type-Options: nosniff` and
  `Content-Security-Policy: sandbox`. Non-media (`kind = document`, any stored MIME, including `text/html`,
  `image/svg+xml`, `application/pdf`, `.exe`) gets `Content-Type: application/octet-stream` and
  `Content-Disposition: attachment; filename*=UTF-8''…` (RFC 5987). HTML, SVG, JS, XML or PDF will not open inline and
  will not run a script. Media is `inline` with its own MIME (it comes from the decoder: only JPEG/PNG/WebP/HEIC/GIF and audio/video from
  the list; SVG is never media), also with `nosniff` and `sandbox`.
- `GET /api/tasks/{id}/archive`: `attachment`, the archive MIME, `nosniff`, `Content-Security-Policy: sandbox`.
- `/thumb` and `/storages/{id}/photo` serve only JPEG that we got from Telegram or made ourselves, with `nosniff`.

## Outgoing requests (URLs and webhooks)

- Only `http`/`https`. A webhook address (`callback_url` and the `uploads.webhook_url` setting) is checked already on input:
  an IP literal of a non-public address is `422 unsafe_url` at once (`integrations/web.check_webhook`).
- DNS is resolved once, **all** addresses must be public (`is_global`): no localhost, LAN, link-local,
  CGNAT, cloud metadata, IPv4-mapped IPv6. For IPv6 with an embedded IPv4 (IPv4-compatible `::/96`, NAT64
  `64:ff9b::/96` and `64:ff9b:1::/48`, `EMBEDDED_V4`) the IPv4 from the low 32 bits goes through the same check
  (`is_global`, not multicast): behind DNS64 public sites resolve to `64:ff9b::a.b.c.d` and stay reachable, while
  `64:ff9b::7f00:1` (127.0.0.1) or `64:ff9b::a9fe:a9fe` (cloud metadata) are rejected. 6to4 `2002::/16`
  and Teredo `2001::/32` are rejected entirely (already not `is_global`). The connection goes to the checked IP, Host and SNI stay the original ones:
  DNS rebinding between the check and the request is impossible.
- Ports: only 80, 443 and ≥ 1024 (`check_scheme`, for both URLs and webhooks), otherwise `422 unsafe_url`, so nobody can
  knock on SSH, SMTP and other system services of other hosts.
- URL downloads send `Accept-Encoding: identity`: we do not ask for a compressed body, and the size limit is counted
  on decompressed bytes anyway.
- At most 5 redirects, each checked again. Proxies from the environment are ignored.
- Timeouts, a hard size limit while reading.
- A webhook is signed with the user's key: `X-Tcloud-Signature: sha256=HMAC(HKDF(APP_SECRET_KEY, "tcloud-webhook:{user_id}"), body)`,
  the owner is in `X-Tcloud-User`; the key is at `GET /api/account/webhook-secret` (background-tasks.md, "Verifying the signature").

## Container and secrets

- The app runs as `appuser` (uid 1000), read-only rootfs, writes go only to the volumes `session`, `tmp` and the tmpfs `/tmp`.
- `.env` and Telegram session files are not committed and do not get into the image (`.dockerignore`: `.env*`, `data/`, `*.session*`);
  `make env` sets `.env` to mode `0600`. Every session file is as good as the account password: mode `0600`,
  sign-in and sign-out only through `/api/telegram` and only for root, the 2FA password is never logged or stored (flows/auth.md).
- loguru is configured with `diagnose=False`: tracebacks carry no variable values, otherwise passwords and keys would end up there.
- An unknown login at sign-in is logged only as `sha256:` + 12 hex characters (a typo in the login is often
  the password), a webhook only as `scheme://host`, without path and query (they may hold tokens).
- Secret lengths are checked when settings load: `APP_SECRET_KEY` ≥ 32 characters, `ROOT_PASSWORD` ≥ 12, otherwise
  the API and workers do not start (`src/config/models/app.py`, `root.py`). `make env` generates both with a margin
  (43 and 24 characters). Values are hidden in settings validation errors (`hide_input_in_errors` in `src/config/base.py`).
- RabbitMQ and Redis have no exposed ports and use passwords (`make env`); Redis holds only task statuses, not files.
- Dependencies are locked in `uv.lock`, `pip-audit` runs in `make check`.
- Files with `encrypt=false` and media (`send_as=media`) sit in the channels as plaintext, so the channels must be private (no username);
  this is checked when connecting from `.env`.

## File encryption

On by default for every upload with `send_as=file`; `send_as=media` without an explicit `encrypt` goes as plaintext,
`encrypt=false` turns it off (flows/uploads.md). It protects against a leak of the Telegram channel or account,
but not against someone who has the server and `.env`: the key is there, and the type checks and ClamAV run on the plaintext file.

- `APP_ENCRYPTION_KEY` is 32 bytes in base64 (`make env` generates it). A wrong format stops the app from starting; empty means
  encryption is off (`503 encryption_unavailable`). The key is never logged. Losing or changing the key makes encrypted
  files unreadable: keep a copy separately; there is no rotation. What to back up and how, and what is lost without each part:
  [deployment.md, "Backups and restore"](deployment.md#backups-and-restore).
- Every file has its own random key (DEK), stored only wrapped by the server key (AES-256-GCM).
- File layout: a 71-byte header (`TCE1`, nonce and the wrapped DEK, a 7-byte random nonce prefix), then blocks of 64 KiB
  of plaintext, each AES-256-GCM with a 16-byte tag. A block nonce = prefix + block number (4 bytes) + a last-block
  flag: reordering, substituting and truncating blocks are detected. Code: `infrastructure/security/encryption.py`.
- The header is both at the start of the file in Telegram and in `files.encryption_header`, so the file can be decrypted without
  the DB (future restore from Telegram).
- In Telegram the name is `{uuid}.bin`, without previews or media attributes; the real name, MIME and `sha256` of the plaintext file live only in
  the DB. `sha256` lets one tell from the DB that two files are identical (the owner's decision).
- A file bigger than one Telegram message (flows/uploads.md, "Big files in parts") is encrypted the same way as a whole, with one
  DEK and one stream, and cut **at block boundaries** (64 KiB + 16-byte tag) when sent: part 0 holds the header and
  the first blocks, the next ones only blocks, named `{uuid}.part{N}.bin` in Telegram. A block nonce contains its number within the whole
  file, so any part can be decrypted alone with the header from the DB and its `offset` (that is how `Range` works), and
  reordering parts breaks the tags. A truncated part is caught by comparing its size with `file_parts.size`, the end of the file by the
  last-block flag.
- Old files are not re-encrypted.
- Previews of encrypted photos, GIFs and videos (≤ 320 px JPEG) are made on the server from the plaintext file and stored only in the DB
  (`files.thumbnail`): `nonce (12 bytes) + AES-256-GCM` with the same file DEK, a random nonce and AAD `TCE1-thumbnail`
  (it does not overlap with the body blocks). The preview never goes to Telegram; it cannot be opened without the server key.
