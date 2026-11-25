# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-11-25

First public release.

### Added

- Storages as private Telegram channels managed through a Telegram user account (Telethon), kept in a "TCloud" chat
  folder; create, update and delete with or without the channel, channel limit reported as `409 channels_limit`.
- Files of any type and size: one Telegram message up to 2 GB (4 GB with Premium), larger files stored as parts.
  Non-media files are served only as sandboxed attachments (`attachment`, `nosniff`, CSP `sandbox`).
- Uploads from a device in one request or as resumable chunked sessions (`/api/uploads/sessions`), from a URL and in
  batches of up to 100 URLs, with SSRF protection.
- Celery pipeline on RabbitMQ with priority queues (`uploads.download`, `uploads.prepare`, `uploads.telegram`,
  `archives.build`), task state in Redis, queue positions, progress, cancel, priority change, retry, idempotent
  sends on redelivery and signed webhooks (per-user HKDF key).
- Content-based type detection and ClamAV scanning; AES-256-GCM encryption enabled by default.
- Folders with paginated server-sorted listing (keyset cursor, ICU collations for `ru`/`en`), subtree sizes and
  file counts; file search with filters and MIME facets; upload activity per day.
- Folder, storage and selection archives (zip, 7z, tar.gz, tar.bz2, tar.xz) built as background tasks with limits.
- File downloads with `Range`, `ETag`/`304` and `If-Range`; thumbnails from Telegram and encrypted previews for
  encrypted files, with root backfill and cleanup.
- Users with `root`, `admin` and `user` roles, JWT (RS256) in HttpOnly cookies, API keys and folder grants.
- Per-user settings synced on the server (`/api/account/settings`), account info and Telegram channel usage.
- Telegram sign-in through the API by QR or code with 2FA for the `api`, `upload` and `download` sessions.
- Per-user temp disk, upload session and Telegram send quotas; shared disk ledger in Redis.
- Security headers, `Origin` check against CSRF, request body cap, sliding-window rate limits per IP and user.
- `GET /api/health` with database, broker, workers, Telegram sessions, ClamAV and Redis checks.
- Docker images for the API and workers, production and development compose files, Flower, load test stand,
  GitHub Actions CI and Dependabot.

[Unreleased]: https://github.com/NikitkaZXC/tcloud-backend/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/NikitkaZXC/tcloud-backend/releases/tag/v1.0.0
