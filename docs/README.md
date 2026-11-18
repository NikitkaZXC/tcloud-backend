# Документация tcloud

Хранилище медиа поверх Telegram: файлы лежат в приватных каналах (хранилищах), у каждого файла свой UUID
и путь `tcloud/{uuid хранилища}/{папки}/{файл}`, всё управляется через REST API.

Краткое описание и быстрый старт — в [`../README.md`](../README.md). Правила для Claude Code — в [`../CLAUDE.md`](../CLAUDE.md).

## Содержание

### Основы

| Документ | О чём |
|---|---|
| [overview.md](overview.md) | что делает сервис, словарь терминов, главные правила, планы |
| [architecture.md](architecture.md) | слои, папки, сервисы, репозитории, фабрики, транзакции, соглашения по коду |
| [database.md](database.md) | таблицы, индексы и зачем они, миграции |
| [security.md](security.md) | доступ, проверка файлов, ClamAV, SSRF, контейнер, секреты |
| [integrations.md](integrations.md) | Telegram (Telethon) и его лимиты, ClamAV, исходящий HTTP |
| [background-tasks.md](background-tasks.md) | Celery и RabbitMQ: очереди загрузок и архивов, приоритеты, воркеры, состояние в Redis, лимиты, FloodWait, повторы, вебхуки |

### Работа с проектом

| Документ | О чём |
|---|---|
| [development.md](development.md) | установка, запуск локально, команды `make`, миграции, форматирование, зависимости |
| [deployment.md](deployment.md) | Docker-образ, compose для прода (API, воркеры, RabbitMQ, Redis), первый вход |
| [testing.md](testing.md) | unit, integration, API и нагрузочные тесты |

### Процессы

| Документ | О чём |
|---|---|
| [flows/users.md](flows/users.md) | пользователи, роли root/admin/user, JWT в куках, API-ключи, гранты на папки |
| [flows/auth.md](flows/auth.md) | вход в Telegram через API: QR, код, 2FA, выход, старые сессии, безопасность |
| [flows/storages.md](flows/storages.md) | хранилища: создание, удаление, подключение из `.env`, название, описание, фото, папка чатов `TCloud` |
| [flows/folders-and-files.md](flows/folders-and-files.md) | пути, папки, метаданные, скачивание с Range и папок архивом, удаление |
| [flows/settings.md](flows/settings.md) | настройки пользователя на сервере: `GET/PATCH/DELETE /api/account/settings`, все ключи, как они подставляются в загрузки и архивы |
| [flows/uploads.md](flows/uploads.md) | загрузка с устройства, по ссылке, пачками; `send_as`, `on_conflict`, `priority`, прогресс |
