# Overview

tcloud puts photos, videos, audio, GIFs and any other files into private Telegram channels through a user account
(Telethon) and exposes them through a REST API. Access is per user with the roles `root` (from `.env`), `admin` and `user`; a user sees only the folders of their grants (flows/users.md).

## Features

- Files of any size: a Telegram message holds up to 2 GB (4 GB with Premium), bigger files go in parts. Sent as the original or as compressed media.
- Uploads from a device in one request, from a URL and in batches of up to 100 URLs; progress, queue position, webhooks.
- Celery queues on RabbitMQ with priority `low/normal/high`: download, checks and sending to Telegram are separate
  workers, statuses in Redis. Cancel, priority change, retry, no duplicates when a worker crashes, Flower and per-worker health.
- Content-based type detection, files of any type, non-media served only as downloads (`attachment`, `nosniff`, CSP `sandbox`), strict ClamAV, SSRF protection.
- Archives of a folder or of selected files and folders (zip, 7z, tar.gz/bz2/xz) as a background task.
- File search by name (case-insensitive, Cyrillic included; name sort in the interface language, `lang=ru|en`) and by filters: type, MIME, size, date, encryption; in a folder, a storage or everywhere (`GET /api/files/search`).
- Upload activity map by day (`GET /api/files/activity`).
- User settings on the server (`GET/PATCH/DELETE /api/account/settings`): defaults for uploads and archives apply on any device and for API keys, plus interface settings and the "TCloud" Telegram chat folder for all storage channels ([flows/settings.md](flows/settings.md)).
- Paginated folder listing (`GET /api/folders`: server-side sort by name/type/size/dates with ICU rules for ru/en, keyset cursor, 20 items per page); folder sizes and file counts in the listing, the account's channel count in `GET /api/account`.
- Read and write indicators per storage for all clients at once (`GET /api/storages/activity`, Redis markers for 5 s).
- Photo and video previews (`GET /api/files/{id}/thumb`, JPEG ≤ 320 px, with `ETag`): from Telegram, and for encrypted files our own, encrypted in the DB; old files without a preview are backfilled by a root background job (`POST /api/files/previews/backfill`); root can switch previews off for everyone (`uploads.previews`) and erase the stored ones (`DELETE /api/files/previews`).
- Uploads from a device in one request or in chunks that resume after a dropped connection (`POST /api/uploads/sessions`, 64 MiB chunks, [flows/uploads.md](flows/uploads.md)).
- Encryption by default (AES-256-GCM, server key `APP_ENCRYPTION_KEY`) for every file except `send_as=media`; `encrypt=false` turns it off. Telegram holds only ciphertext.
- Telegram sign-in through the API: QR or code, two-step verification, old tcloud sessions are revoked automatically.
- A file deleted by hand in Telegram drops its DB row automatically (Telethon events, plus a check on download).
- FastAPI, PostgreSQL, RabbitMQ, Redis, Celery, Python 3.14 (uv), everything in Docker.

## Glossary

| Term | What it is |
|---|---|
| **Storage** | a private Telegram channel. The DB holds only our UUID and `tg_chat_id`; title, description and photo live in Telegram |
| **Path** | `tcloud/{storage uuid}/{folders}/{name}`; folders exist only in the DB |
| **File** | one message in the channel. The caption carries the token `#tc<uuid without hyphens>` for searching inside Telegram |
| **Task** | one upload or one folder archive build: `queued → running → done/failed/cancelled`, stages `downloading → scanning → uploading_tg → done` (archive: `archiving → done`); lives in Redis for 7 days |
| **Queue** | RabbitMQ: `uploads.download`, `uploads.prepare` (checks), `uploads.telegram` (sending) and `archives.build` (folder archives), with priority `low/normal/high` |
| **Batch** | up to 100 URL uploads in one `POST /api/uploads/batch`, with shared progress |

## Main rules

- All our ids are UUIDs (v7: they grow over time, indexes do not fragment). Numbers are only Telegram ids.
- We do not store the Telegram `file_id`: it changes, and `file_reference` expires. We store `chat_id` + `message_id`,
  and the message is fetched again on download.
- Media and files of any type; non-media is served only as a download (`attachment` + `sandbox`). The type is detected by content,
  ClamAV checks every file. Files are never executed.
- A Telegram message holds up to 2 GB, 4 GB with Premium (Premium is detected automatically); a bigger file goes in parts,
  the overall limit is root's `uploads.max_file_size` setting (unlimited by default).
- Uploads go through Celery workers; reads from Telegram happen in the API. `worker-telegram` is a single replica.

## Out of scope for now (plans)

- Splitting big files into parts and joining them on download.
- Encrypting files with a user secret (currently the server key, on by default for `send_as=file`).
- Moving and renaming files and folders.
- Periodic reconciliation of the DB with Telegram (deletions missed during a long API downtime).
- Rebuilding the DB from Telegram using the UUIDs in captions.
