# tcloud documentation

Media storage on top of Telegram: files live in private channels (storages), every file has its own UUID
and a path `tcloud/{storage uuid}/{folders}/{file}`, everything is managed through a REST API.

The entry point is [`../README.md`](../README.md) (launch, commands, settings, tests). Rules for Claude Code are in
[`../CLAUDE.md`](../CLAUDE.md). This folder explains how everything works, with Mermaid diagrams checked against the code and paths to the files
where it is done. How the frontend uses this API is in `../tcloud-frontend/docs/`.

**Rule: a code change that alters a flow shown in `docs/` updates the document and its diagram in the same
commit.** A new diagram is a ` ```mermaid ` block; before committing check that it renders (`mmdc` from
`@mermaid-js/mermaid-cli`, installed outside the repository). Docs are written in English.

## Contents

### Basics

| Document | About |
|---|---|
| [overview.md](overview.md) | what the service does, glossary, main rules, plans |
| [architecture.md](architecture.md) | component diagram (API, workers, PostgreSQL, Redis, RabbitMQ, ClamAV, Telegram, nginx), layers, folders, services, transactions, conventions |
| [database.md](database.md) | ER diagram, tables, indexes and why they exist, migrations |
| [redis.md](redis.md) | Redis key map: tasks, queues, locks, heartbeat, activity, tokens |
| [security.md](security.md) | access, file checks, ClamAV, SSRF, container, secrets |
| [integrations.md](integrations.md) | Telegram (Telethon) and its limits, ClamAV, outgoing HTTP |
| [background-tasks.md](background-tasks.md) | Celery and RabbitMQ: queue diagram, upload path (sequence), statuses and waiting codes (state), priorities, cancel, limits, FloodWait, webhooks |

### Working on the project

| Document | About |
|---|---|
| [development.md](development.md) | install, local run, `make` commands, migrations, formatting, dependencies |
| [deployment.md](deployment.md) | Docker image, production compose (API, workers, RabbitMQ, Redis), first sign-in, [backups and restore](deployment.md#backups-and-restore) |
| [testing.md](testing.md) | unit, integration, API and load tests |

### Flows

| Document | About |
|---|---|
| [flows/users.md](flows/users.md) | users and roles; diagrams of sign-in, refresh with reuse detection and request checks (cookie or API key); grants |
| [flows/auth.md](flows/auth.md) | Telegram sign-in through the API: three sessions, login states, what happens after sign-in, old sessions |
| [flows/storages.md](flows/storages.md) | storages: create and delete (diagram), connecting from `.env`, the `TCloud` chat folder (diagram), channel count cache (diagram), read and write indicators |
| [flows/folders-and-files.md](flows/folders-and-files.md) | paths, folders, metadata, download with Range and decryption (diagram), archives (diagram), previews, search, delete |
| [flows/settings.md](flows/settings.md) | user settings: tolerant read, strict `PATCH`, merge (diagram), all keys, defaults for uploads and archives |
| [flows/uploads.md](flows/uploads.md) | upload from a device, from a URL, in batches; `send_as`, `on_conflict`, `encrypt`, progress, waiting and warning codes |
