# Архитектура

Слои, зависимости идут только вниз: `api → application → infrastructure`; `utilities` доступны всем.
`src/application` никогда не импортирует `src/api`, `src/infrastructure` — ни то, ни другое.

```
main.py                    FastAPI-приложение, lifespan: БД, Telegram, ClamAV, Redis, AppRuntime, подготовка при старте
src/
  config/                  настройки (pydantic-settings), по классу на группу: APP_, POSTGRES_, TELEGRAM_, CLAMAV_, UPLOAD_, RABBITMQ_, REDIS_, LOG_
  api/
    endpoints.py           все роутеры под /api; /api/telegram только для root
    middleware/            заголовки безопасности, проверка Origin (CSRF), см. security.md; CORS подключён в main.py
    routes/                system, storage, folder, upload, file
    schemas/               HTTP-схемы запросов и ответов
    exception/             код ошибки → HTTP-статус, обработчики, единый формат {"error": {...}}
    dependencies/          verify_api_key
  application/
    services/              бизнес-логика: Account, Auth (вход в Telegram, чистка старых сессий), Health, Storage, Folder, File, Task, Upload, Settings (настройки пользователя, flows/settings.md), UploadPipeline (стадии воркеров),
                           TaskSweeper (потерянные задачи, крутится в API)
    tasks/                 Celery-задачи uploads.download, uploads.prepare, uploads.telegram, archives.build, WorkerRuntime и Heartbeat воркера
    schemas/               DTO сервисов (*ServiceRead, *ServiceCreate, *ServiceUpdate)
    factory/               FastAPI-зависимости get_service_*: сервисы роуты получают только через них
    exception/             доменные ошибки
    bootstrap/             startup (старые файлы tmp; если API-сессия вошла — каналы из .env и события удалений)
  infrastructure/
    database/              модели *DB, миксины (UUIDv7 id, created_at, updated_at), репозитории, миграции
    integrations/          telegram (Telethon), antivirus (clamd), web (безопасные исходящие запросы)
    media/                 определение типа, Pillow/ffprobe, превью, перевод в JPEG
    background/            Celery-приложение и очереди, enqueue, queue_sizes, DiskBudget (резерв места), AppRuntime (состояние API),
                           WorkerRuntime (event loop, Telethon, БД и Redis воркера), Heartbeat
    cache/                 Redis: TaskStore — задачи, пачки, блокировки путей, активные задачи, Lua CLAIM/REQUEUE/CANCEL
    enums/, logger/, limits.py
  utilities/
    errors.py              TCloudError: базовая ошибка с кодом
    parsers/               пути tcloud/..., заголовок Range
    formatters/            ограниченные pydantic-типы для запросов
```

## Сервисы, репозитории, транзакции

- Сервис получает `AsyncSession` и `AppRuntime`, репозитории создаёт сам в `__init__`.
  Другие сервисы передаются в конструктор (`FolderService(storage_service=...)`).
- Репозитории только `flush()`; **коммитит сервис**.
- `UploadPipeline` работает в воркере Celery и открывает короткие сессии через `WorkerRuntime.session_factory`
  (свой движок с `NullPool` в event loop воркера). API ставит задачи через `enqueue` по имени — без импорта задач.
- После ORM-`UPDATE` c `onupdate=func.now()` SQLAlchemy помечает `updated_at` устаревшим; `get_by_id` в репозиториях
  читают с `populate_existing=True`, иначе ленивая подгрузка в async-коде падает с `MissingGreenlet`.

## Ошибки

Каждая ошибка — подкласс `TCloudError` со стабильным `code` (`storage_not_found`, `too_large`, `flood_wait`…).
Инфраструктура бросает свои (`UnsupportedMediaError`, `UnsafeUrlError`, `TelegramFloodWaitError`), приложение — доменные
(`PathConflictError`, `UploadInProgressError`). HTTP-статус выбирает только слой API — по коду, в `api/exception/errors.py`.
Telethon-исключения наружу не выходят: `translate_errors` превращает их в `TelegramFloodWaitError`,
`TelegramUnavailableError` (можно повторить) и `TelegramError`.

## Соглашения

- Комментарии и docstrings в коде не пишем; исключение — однострочный docstring у класса исключения.
  Всё «почему» — здесь, в `docs/`.
- Логи — loguru: info на ключевые действия, warning на пропуски и проглоченные ошибки, exception на сбои. Секреты не логируем.
- Миграции не редактируем, изменения схемы — новой ревизией; `alembic check` в тестах ловит расхождение моделей и миграций.
- Пакеты реэкспортируют публичные имена через `__init__.py` с `__all__`.
- Форматирование — black + isort (`make format`).
- Коммиты — одна строка на английском (`feat: ...`, `fix: ...`, `refactor: ...`), без push.
