# База данных

PostgreSQL 18, SQLAlchemy 2 async (asyncpg), миграции Alembic в `src/infrastructure/database/migrations/`.
Все первичные ключи — UUID (v7, генерируются в Python).

| Таблица | Что хранит | Индексы и зачем |
|---|---|---|
| `storages` | `id`, `tg_chat_id`, даты. Название, описание, фото — только в Telegram | `UNIQUE(tg_chat_id)` — канал подключается один раз |
| `folders` | `storage_id`, `path` (`a/b/c`, без корня) | `UNIQUE(storage_id, path)`; `(storage_id, path text_pattern_ops)` — `LIKE 'a/%'` для дочерних папок и рекурсивного удаления |
| `users` | `login`, argon2 `password_hash`, `role` (`root`/`admin`/`user`), `blocked`, `failed_logins`, `locked_until`, `last_login_at` | `UNIQUE(login)` — вход по логину; частичный `UNIQUE(role) WHERE role = 'root'` — root один |
| `refresh_tokens` | `user_id`, HMAC `token_hash`, `access_jti`, user-agent, IP, `signed_in_at`, `expires_at`, `revoked` | `UNIQUE(token_hash)` — поиск при refresh; `user_id` — сессии пользователя, отзыв всех |
| `api_keys` | `user_id`, `name`, HMAC `key_hash`, `last_used_at` | `UNIQUE(key_hash)` — проверка `X-API-Key`; `user_id` — список ключей |
| `folder_grants` | `user_id`, `storage_id`, `path` (папка без корня, `''` — всё хранилище), `recursive`, `can_read`, `can_upload`, `can_delete` | `UNIQUE(user_id, storage_id, path)` — один грант на папку и гранты пользователя на каждый запрос; `storage_id` — каскадное удаление с хранилищем |
| `files` | путь (`folder_path` + `name`), размер, MIME, тип (`photo/video/audio/animation/document`), `sent_as`, размеры, длительность, `sha256`, `tg_message_id` | `UNIQUE(storage_id, folder_path, name)` — один файл на путь; `(storage_id, folder_path text_pattern_ops)` — листинг и удаление поддерева; `UNIQUE(storage_id, tg_message_id)`; `sha256` |

`_` и `%` в именах папок экранируются перед `LIKE` (`\` в путях запрещён, им и экранируем).
Задачи загрузки в БД не хранятся: очередь — RabbitMQ, статус и прогресс — Redis (см. background-tasks.md).

## Миграции

| Ревизия | Что |
|---|---|
| `0001` | storages, folders, files, tasks |
| `0002` | `tasks.path`, `tasks.batch_id`, индексы для пачек и списков, уникальность активного пути |
| `0003` | удалена таблица `tasks`: состояние загрузок переехало в Redis |
| `0004` | `users`, `refresh_tokens`, `api_keys`, `folder_grants` (flows/users.md) |

Новая ревизия: `uv run alembic revision --autogenerate -m "..."`. Существующие файлы не правим.
`tests/integration/test_migrations.py` проверяет, что модели совпадают с миграциями и что всё откатывается до `base` и обратно.
