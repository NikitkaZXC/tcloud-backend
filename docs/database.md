# База данных

PostgreSQL 18, SQLAlchemy 2 async (asyncpg), миграции Alembic в `src/infrastructure/database/migrations/`.
Все первичные ключи — UUID (v7, генерируются в Python).

| Таблица | Что хранит | Индексы и зачем |
|---|---|---|
| `storages` | `id`, `tg_chat_id`, даты. Название, описание, фото — только в Telegram | `UNIQUE(tg_chat_id)` — канал подключается один раз |
| `folders` | `storage_id`, `path` (`a/b/c`, без корня) | `UNIQUE(storage_id, path)`; `(storage_id, path text_pattern_ops)` — `LIKE 'a/%'` для дочерних папок и рекурсивного удаления |
| `files` | путь (`folder_path` + `name`), размер, MIME, тип, `sent_as`, размеры, длительность, `sha256`, `tg_message_id` | `UNIQUE(storage_id, folder_path, name)` — один файл на путь; `(storage_id, folder_path text_pattern_ops)` — листинг и удаление поддерева; `UNIQUE(storage_id, tg_message_id)`; `sha256` |
| `tasks` | загрузка: `path`, `batch_id`, `status`, `stage`, байты, `error`, `warnings`, `callback_url`, `params` | `ux_tasks_active_path` — уникальный `path` среди `pending/running` (две загрузки в одно место решает БД); `(status, created_at)` — истечение и подсчёт активных; `(created_at)` — список; `(batch_id, created_at)` — пачка |

`_` и `%` в именах папок экранируются перед `LIKE` (`\` в путях запрещён, им и экранируем).
Живой прогресс (байты, скорость, место в очереди) в БД не пишется — только смена стадий и итог.

## Миграции

| Ревизия | Что |
|---|---|
| `0001` | storages, folders, files, tasks |
| `0002` | `tasks.path`, `tasks.batch_id`, индексы для пачек и списков, уникальность активного пути |

Новая ревизия: `uv run alembic revision --autogenerate -m "..."`. Существующие файлы не правим.
`tests/integration/test_migrations.py` проверяет, что модели совпадают с миграциями и что всё откатывается до `base` и обратно.
