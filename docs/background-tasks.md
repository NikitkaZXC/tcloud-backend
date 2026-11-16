# Фоновая обработка загрузок

Загрузки обрабатывают воркеры Celery. Брокер — RabbitMQ, состояние задач хранится в Redis. Таблицы задач в PostgreSQL нет.

```
API ──(файл в общий том /data/tmp)──► uploads.prepare ──► worker-prepare
                                                           │ скачивание по ссылке, magic bytes, Pillow/ffprobe,
                                                           │ sha256, ClamAV, JPEG для фото, превью видео
                                      uploads.telegram ◄──┘
                                             │
                                             ▼
                                      worker-telegram (своя сессия Telethon)
                                      отправка в TG → запись files → вебхук
```

- **API** принимает файл и пишет его в `UPLOAD_TMP_DIR`, общий том `tmp` для API и воркеров. Затем API создаёт задачу в
  Redis и ставит её в очередь `uploads.prepare`. Загрузка по ссылке сразу уходит в очередь, скачивает её воркер.
- **worker-prepare** (`--concurrency=UPLOAD_PREPARE_CONCURRENCY`, по умолчанию 4) делает всё, что не касается Telegram,
  и ставит задачу в `uploads.telegram`. Медленная ссылка или ClamAV не занимают слоты Telegram.
- **worker-telegram** (`--concurrency=TELEGRAM_CONCURRENCY`, по умолчанию 2) отправляет файл в Telegram, записывает строку
  в `files` и удаляет временные файлы. Работает с сессией `TELEGRAM_WORKER_SESSION` (см. integrations.md).
- **Чтение из Telegram** (скачивание, превью, каналы) остаётся в API: очередь не подходит для потоковой отдачи с Range.

## Воркер изнутри

Celery-задача синхронная, а Telethon, asyncpg и redis — асинхронные. `WorkerRuntime` держит в процессе воркера **один**
event loop в отдельном потоке. Задачи пула `threads` отдают ему корутины и ждут результат. Отсюда:

- один клиент Telethon и одно соединение на процесс, сессия не открывается из нескольких потоков;
- пауза FloodWait общая для всех задач процесса, поэтому `worker-telegram` запускается **одним процессом** (реплика одна);
- `worker-prepare` можно масштабировать репликами, общего состояния у него нет.

Настройки Celery (`src/infrastructure/background/celery/app.py`): `acks_late` и `reject_on_worker_lost`, то есть если
воркер упал, задача вернётся в очередь. `prefetch_multiplier=1` нужен, чтобы приоритет соблюдался: воркер не набирает
задачи впрок. RabbitMQ по умолчанию закрывает канал, если задача не подтверждена за 30 минут. Выгрузка 4 ГБ может
идти дольше, поэтому `consumer_timeout` поднят до 24 часов (`RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS` в compose).

## Приоритет

`priority`: `low` (1), `normal` (5, по умолчанию), `high` (9). Обе очереди объявлены с `x-max-priority=9`, приоритет
передаётся из `uploads.prepare` в `uploads.telegram`. Задача `high` обгоняет уже стоящие в очереди `low`, но не
прерывает те, что выполняются. Тест — `tests/integration/test_queue_priority.py` на настоящем RabbitMQ.

## Статусы и стадии

```
status  queued → running → done | failed
stage   downloading (только по ссылке) → scanning → uploading_tg → done
```

Пока задача ждёт воркера, `waiting = {"reason": "queue:uploads.prepare"}` или `"queue:uploads.telegram"`. Место в
очереди не показывается: RabbitMQ его не сообщает. Во время работы видны `bytes_done`, `progress`, `speed`, `eta`.
Прогресс пишется в Redis не чаще раза в `UPLOAD_WEBHOOK_INTERVAL`.

## Redis

Ключи с префиксом `REDIS_PREFIX` (`tcloud`):

| Ключ | Что | TTL |
|---|---|---|
| `tcloud:task:{id}` | hash: поля задачи, каждое в JSON. Поля пишутся по отдельности, поэтому запись прогресса не затирает статус | `UPLOAD_TASK_TTL` (7 дней) |
| `tcloud:batch:{id}` | список id задач пачки по порядку | `UPLOAD_TASK_TTL` |
| `tcloud:path:{path}` | блокировка пути, значение — id задачи. Снимается только владельцем (Lua) | `UPLOAD_LOCK_TTL` (24 ч) |
| `tcloud:active` | sorted set активных задач, score — время старта. Записи старше `UPLOAD_LOCK_TTL` выбрасываются при подсчёте | — |

Через 7 дней задача пропадает из Redis, и `GET /api/tasks/{id}` отвечает `404`. Сам файл остаётся в `files`.

## Защита ресурсов

- **Диск.** `DiskBudget` резервирует место до приёма тела в API и при скачивании по ссылке в воркере. Если после
  загрузки останется меньше `UPLOAD_MIN_FREE_DISK_MB`, ответ `507 insufficient_storage`.
- **Число задач.** Если активных больше `UPLOAD_MAX_ACTIVE_TASKS`, ответ `429 too_many_tasks` с `retry_after`.
- **Один путь — одна загрузка.** `SET NX` по пути: вторая загрузка в тот же путь сразу получает `409 upload_in_progress`.
  `keep_both` ищет свободное имя (`a (1).jpg`) среди файлов и занятых путей.

## Сбои

- **FloodWait.** Короткий (≤ 60 с) Telethon пережидает сам. Если ожидание дольше, но не больше `TELEGRAM_FLOOD_MAX_WAIT`,
  вся выгрузка `worker-telegram` встаёт на паузу (`waiting.reason = flood_wait`). Если ещё дольше, задача падает с `retry_after`.
- **Сеть Telegram и ClamAV.** `UPLOAD_RETRIES` повторов с паузой `UPLOAD_RETRY_DELAY × 2^n` (`waiting.reason = retry`).
- **Запись в БД не удалась** после отправки: сообщение в Telegram удаляется.
- **Рестарт API** задачи не трогает: они в RabbitMQ и Redis. Временные файлы старше `UPLOAD_LOCK_TTL` удаляются при старте.
- **Воркер упал посреди задачи.** RabbitMQ отдаст её снова, и стадия начнётся заново. Если сбой пришёлся на окно между
  отправкой в Telegram и подтверждением задачи, в канале останется дубль сообщения. Строка в `files` будет одна.
- **Брокер недоступен** при постановке в очередь: `503 queue_unavailable`, путь освобождается.

## Вебхуки

На каждой смене стадии и не чаще раза в `UPLOAD_WEBHOOK_INTERVAL` во время прогресса воркер отправляет на `callback_url`
POST с телом задачи. Подпись — `X-Tcloud-Signature`. При сетевых ошибках и 5xx делается 3 повтора. Адрес проходит ту же
проверку, что и ссылки.

## Пачки

`POST /api/uploads/batch`: до 100 загрузок по ссылкам, каждая создаётся отдельно, ошибка одной не мешает остальным.
`GET /api/tasks?batch_id=...` возвращает задачи пачки и сводку: число по статусам, байты, сколько закончено и общий процент.
