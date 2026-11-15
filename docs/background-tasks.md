# Фоновая обработка загрузок

Отдельного брокера нет: конвейер работает в процессе API (`UploadPipeline`), общее состояние — `BackgroundRuntime`
в `app.state`. Поэтому uvicorn запускается **одним процессом**.

## Стадии

```
receiving     приём тела (PUT) или скачивание по ссылке   — лимит UPLOAD_DOWNLOAD_CONCURRENCY для ссылок
scanning      magic bytes, Pillow/ffprobe, sha256, ClamAV, превью, JPEG   — лимит UPLOAD_SCAN_CONCURRENCY
uploading_tg  выгрузка в Telegram                          — лимит TELEGRAM_CONCURRENCY
done | failed
```

Пока задача ждёт слот, `GET /api/tasks/{id}` показывает `waiting: {"reason": "queue:<стадия>"}` и `queue_position`
(очередь FIFO). Во время работы — `bytes_done`, `progress`, `speed`, `eta`.

## Защита ресурсов

- **Диск.** `DiskBudget` резервирует место до приёма файла: принятые, но ещё не записанные байты считаются занятыми.
  Если после загрузки останется меньше `UPLOAD_MIN_FREE_DISK_MB` — `507 insufficient_storage`. Для ссылки без
  `Content-Length` место проверяется на каждом куске.
- **Число задач.** Больше `UPLOAD_MAX_ACTIVE_TASKS` активных — `429 too_many_tasks` с `retry_after`.
  Задача без тела дольше `UPLOAD_PENDING_TTL` становится `failed: expired` и освобождает путь.
- **Один путь — одна загрузка.** Частичный уникальный индекс по `tasks.path` среди активных: вторая загрузка в тот же
  путь сразу получает `409 upload_in_progress`. `keep_both` резервирует свободное имя (`a (1).jpg`) при создании задачи.

## Сбои

- **FloodWait.** Короткий (≤ 60 с) Telethon пережидает сам. Дольше, но не больше `TELEGRAM_FLOOD_MAX_WAIT`, — вся выгрузка
  в TG ставится на паузу (`waiting.reason = flood_wait`), задачи продолжают после неё. Больше — задача падает с `retry_after`.
- **Сеть Telegram и ClamAV.** `UPLOAD_RETRIES` повторов с паузой `UPLOAD_RETRY_DELAY × 2^n` (`waiting.reason = retry`).
- **Запись в БД не удалась** после отправки — сообщение в Telegram удаляется, мусора не остаётся.
- **Рестарт.** Незавершённые задачи становятся `failed: interrupted`, временные файлы удаляются.

## Вебхуки

`callback_url` получает POST с телом задачи при смене стадии и не чаще раза в `UPLOAD_WEBHOOK_INTERVAL` с прогрессом.
Подпись — `X-Tcloud-Signature`. 3 повтора при сетевых ошибках и 5xx. Адрес проходит ту же проверку, что и ссылки.

## Пачки

`POST /api/uploads/batch` — до 100 пунктов (с устройства — `size`, по ссылке — `url`). Каждый создаётся отдельно:
ошибка одного не мешает остальным. `GET /api/tasks?batch_id=...` — задачи пачки и сводка: число по статусам,
байты, сколько закончено и общий процент.
