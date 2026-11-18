# Документация tcloud

Хранилище медиа поверх Telegram: файлы лежат в приватных каналах (хранилищах), у каждого файла свой UUID
и путь `tcloud/{uuid хранилища}/{папки}/{файл}`, всё управляется через REST API.

Точка входа — [`../README.md`](../README.md) (запуск, команды, настройки, тесты). Правила для Claude Code — в
[`../CLAUDE.md`](../CLAUDE.md). Здесь — как всё устроено, со схемами Mermaid, сверенными с кодом, и путями к файлам,
где это сделано. Как этим API пользуется фронтенд — в `../tcloud-frontend/docs/`.

**Правило: изменение кода, которое меняет поток, показанный в `docs/`, обновляет документ и его схему в том же
коммите.** Новая схема — блок ` ```mermaid `; перед коммитом проверьте, что она рендерится (`mmdc` из
`@mermaid-js/mermaid-cli`, установленный вне репозитория).

## Содержание

### Основы

| Документ | О чём |
|---|---|
| [overview.md](overview.md) | что делает сервис, словарь терминов, главные правила, планы |
| [architecture.md](architecture.md) | схема компонентов (API, воркеры, PostgreSQL, Redis, RabbitMQ, ClamAV, Telegram, nginx), слои, папки, сервисы, транзакции, соглашения |
| [database.md](database.md) | ER-схема, таблицы, индексы и зачем они, миграции |
| [redis.md](redis.md) | карта ключей Redis: задачи, очереди, блокировки, heartbeat, активность, токены |
| [security.md](security.md) | доступ, проверка файлов, ClamAV, SSRF, контейнер, секреты |
| [integrations.md](integrations.md) | Telegram (Telethon) и его лимиты, ClamAV, исходящий HTTP |
| [background-tasks.md](background-tasks.md) | Celery и RabbitMQ: схема очередей, путь загрузки (sequence), статусы и коды ожидания (state), приоритеты, отмена, лимиты, FloodWait, вебхуки |

### Работа с проектом

| Документ | О чём |
|---|---|
| [development.md](development.md) | установка, запуск локально, команды `make`, миграции, форматирование, зависимости |
| [deployment.md](deployment.md) | Docker-образ, compose для прода (API, воркеры, RabbitMQ, Redis), первый вход |
| [testing.md](testing.md) | unit, integration, API и нагрузочные тесты |

### Процессы

| Документ | О чём |
|---|---|
| [flows/users.md](flows/users.md) | пользователи и роли; схемы входа, refresh с reuse detection и проверки запроса (кука или API-ключ); гранты |
| [flows/auth.md](flows/auth.md) | вход в Telegram через API: три сессии, состояния входа, что происходит после входа, старые сессии |
| [flows/storages.md](flows/storages.md) | хранилища: создание и удаление (схема), подключение из `.env`, папка чатов `TCloud` (схема), кеш числа каналов (схема), индикаторы чтения и записи |
| [flows/folders-and-files.md](flows/folders-and-files.md) | пути, папки, метаданные, скачивание с Range и расшифровкой (схема), архивы (схема), превью, поиск, удаление |
| [flows/settings.md](flows/settings.md) | настройки пользователя: терпимое чтение, строгий `PATCH`, слияние (схема), все ключи, подстановка в загрузки и архивы |
| [flows/uploads.md](flows/uploads.md) | загрузка с устройства, по ссылке, пачками; `send_as`, `on_conflict`, `encrypt`, прогресс, коды ожидания и предупреждений |
