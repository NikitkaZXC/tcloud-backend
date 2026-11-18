# Загрузки

## С устройства — одним запросом

`POST /api/uploads?path=...&send_as=...&on_conflict=...&priority=...&allow_unscannable=...&encrypt=...&callback_url=...`, тело — сам файл.
Размер берётся из `Content-Length` (без него `411 length_required`). Все проверки идут до чтения тела: путь, расширение,
лимит аккаунта, место на диске, число активных задач, конфликт, занятость пути. Затем тело пишется во временный файл,
и задача встаёт в очередь. Ответ `202` с задачей `queued`. Весь путь по очередям и воркерам со схемами —
background-tasks.md («Путь загрузки целиком», «Статусы и стадии»).

Не переданные `send_as`, `on_conflict`, `priority`, `allow_unscannable`, `encrypt` и `callback_url` берутся из
настроек пользователя (`uploads.*`, flows/settings.md); значения «по умолчанию» ниже — это значения настроек по
умолчанию. Явный параметр всегда главнее. Пустой `callback_url=` выключает вебхук из настроек для одной загрузки.
Тело больше заявленного → `413`, меньше → `400 size_mismatch`; в обоих случаях задача не создаётся.

## По ссылке

`POST /api/uploads/from-url {url, path, send_as, on_conflict, priority, allow_unscannable, encrypt, callback_url}`: имя файла берётся из `path`,
а не из ссылки. Скачивает worker-download с теми же проверками и лимитами. Если первые байты — исполняемый файл, соединение
обрывается.

## Пачкой

`POST /api/uploads/batch {items: [...]}`: до 100 загрузок по ссылкам (поля как у `from-url`). В ответе `batch_id`
и по каждому пункту `task` или `error`. Общий прогресс: `GET /api/tasks?batch_id=`. Файлы с устройства загружаются
параллельными `POST /api/uploads`, очередь и лимиты одни и те же.

## `priority`

`low`, `normal` (по умолчанию), `high`. Задачи `high` берутся из очереди раньше, см. background-tasks.md.

## Управление задачей

| Запрос | Что |
|---|---|
| `POST /api/tasks/{id}/cancel` | отмена: из очереди — сразу, во время работы — в течение пары секунд; `409 task_finished` для завершённых |
| `POST /api/tasks/{id}/priority {"priority": "high"}` | новый приоритет задачи, которая ждёт в очереди; иначе `409 task_not_queued` |
| `POST /api/tasks/{id}/retry` | повтор упавшей или отменённой загрузки по ссылке; с устройства — `409 task_not_retryable` |

## Типы файлов

Медиа (`kind`: `photo`, `video`, `audio`, `animation`) и всё остальное (`kind = document`, MIME по содержимому или
`application/octet-stream`). Исполняемые файлы, скрипты и установщики — `415 blocked_file_type`, в том числе внутри zip.
Все проверки — в security.md.

## `allow_unscannable`

`false` по умолчанию: зашифрованные архивы и PDF, Office с макросами и файлы, на которых ClamAV упёрся в лимит, —
`unscannable`. `true` — такие файлы принимаются, в задаче предупреждение. Вирусы отклоняются в любом случае.

## `encrypt`

По умолчанию включено (решение владельца): параметр не передан и `send_as=file` (тоже по умолчанию) — файл шифруется
ключом сервера `APP_ENCRYPTION_KEY` перед отправкой в Telegram (security.md, «Шифрование файлов»). Параметр не передан и
`send_as=media` — шифрование молча выключается, Telegram должен видеть настоящее медиа. Явный `encrypt=true` с
`send_as=media` → `422 encrypt_requires_file`; явный `encrypt=false` — файл уходит открытым. Одинаково для
`POST /api/uploads`, `/uploads/from-url` и каждого элемента `/uploads/batch`. Не переданный `encrypt` берётся из
настройки `uploads.encrypt`, но с итоговым `send_as=media` (из запроса или из настройки) всё равно молча выключается. Шифрование нужно, а ключ не
задан → `503 encryption_unavailable` (в том числе для загрузки по умолчанию: передайте `encrypt=false` или задайте ключ).
Превью зашифрованных фото, GIF и видео делаем сами на стадии `scanning`, пока файл ещё открыт: Pillow (видео — кадр
ffmpeg, как для открытых видео), JPEG ≤ 320 px, качество снижается до ≤ 20 КБ, ориентация по EXIF. Превью шифруется
DEK файла и ложится в `files.thumbnail`; `/thumb` его расшифровывает. Ошибка генерации не валит загрузку: предупреждение
в лог, `thumbnail = NULL`, `/thumb` → `404`. У аудио и документов превью нет. Файлы, зашифрованные до миграции `0009`
(и с ошибкой генерации), дозаполняет фоновая задача root `POST /api/files/previews/backfill` (background-tasks.md,
«Дозаполнение превью»). Проверки типа и ClamAV идут по открытому файлу, как обычно. В Telegram уходит
документ `{uuid}.bin` без превью и атрибутов, в БД — настоящие имя, MIME, тип, размер и `sha256` открытого файла.
Лимит файла меньше лимита Telegram на накладные расходы (≈16 байт на 64 КиБ + 71 байт заголовка). На стадии
`scanning` на диске одновременно лежат открытый файл и шифротекст; открытый удаляется сразу после передачи в очередь
`uploads.telegram`.

## `send_as`

| Значение | Фото | Видео | Аудио | GIF | Документ |
|---|---|---|---|---|---|
| `file` (по умолчанию) | документ, оригинал | документ | документ | документ | документ |
| `media` | сжатое фото; не влезло в лимиты → документ с предупреждением; webp/heic/gif → JPEG | оригинал с атрибутами, превью и плеером | плеер, длительность, теги | анимация | документ с предупреждением |

## `on_conflict`

| Значение | Если путь занят файлом |
|---|---|
| `error` (по умолчанию) | `409 conflict` с `suggested_name` — свободное имя для `keep_both` |
| `replace` | новое сообщение, UUID сохраняется, `updated_at` обновляется, старое сообщение удаляется |
| `keep_both` | свободное имя с номером через дефис: `a-2.jpg`, `a-3.jpg`, без расширения `README-2`; резервируется сразу при создании задачи |
| `skip` | задача сразу `done` со ссылкой на существующий файл, тело не читается |

Если в этот путь уже идёт другая загрузка — `409 upload_in_progress` при любом режиме.

Диалог «файл уже есть» на фронтенде: загрузить с `error`; на `409 conflict` показать выбор «Заменить» (повтор с
`replace`, нужны права на удаление), «Оставить оба» (повтор с `keep_both`, имя из `suggested_name` — то, что получится,
если никто не займёт его раньше) и «Отмена» (ничего не отправлять). `skip` — то же, что «Отмена», но без вопроса.

## Прогресс

`GET /api/tasks/{id}`: `status` (`queued/running/done/failed/cancelled`), `stage`, `priority`, `bytes_done`/`bytes_total`,
`progress`, `speed`, `eta`, `queue_position` (номер в очереди, 1 — следующая), `waiting` (очередь, FloodWait, повтор),
`cancel_requested`, `error`, `warnings`, `warning_details`, `file`.
Задача хранится 7 дней (`UPLOAD_TASK_TTL`). Вебхук описан в background-tasks.md.

Опрос многих задач одним запросом: `GET /api/tasks?ids={id}&ids={id}…` — до 100 id (больше → `422 validation_error`),
повторы схлопываются; ответ как у пачки — `{items, summary}`, в `items` только существующие задачи, которые вы видите
(свои; admin и root — любые), в порядке `ids`; чужие и пропавшие молча отсутствуют. Ровно один из `ids` / `batch_id`,
иначе `422`. Один `HGETALL` на id в одном pipeline Redis и один запрос в БД за файлами.

`GET /api/tasks/counts` → `{total, by_status: {"done": 12, "failed": 1, …}}` — ваши задачи (загрузки и архивы,
созданные вами, у admin тоже только свои) за `UPLOAD_TASK_TTL`; статусы без задач в `by_status` не попадают. Индекс —
sorted set `user_tasks:{user_id}` в Redis, подсчёт — один `HGET status` на задачу в pipeline.

### Коды ожидания и предупреждений

Тексты (`waiting.reason`, `warnings`, `error.message`) — английские и для людей; фронтенд переводит по кодам.
Коды стабильны, новые могут добавляться — неизвестный код показывайте текстом `message`.

`waiting = {reason, code, params, seconds}` (`seconds` — сколько ещё ждать, если известно):

| `code` | `params` | Когда |
|---|---|---|
| `queue` | `queue`: `uploads.download` / `uploads.prepare` / `uploads.telegram` / `archives.build` | ждёт воркера в очереди (`reason` = `queue:{queue}`), номер — `queue_position` |
| `disk_slots` | — | ждёт места на диске сервера (`UPLOAD_MAX_FILES_ON_DISK`) |
| `retry` | — | сеть Telegram или ClamAV недоступна, повтор через `seconds` |
| `flood_wait` | — | Telegram попросил паузу (FloodWait), вся выгрузка ждёт `seconds` |

`warnings` — список английских строк (как раньше), `warning_details` — те же предупреждения по порядку как
`{code, params, message}`:

| `code` | `params` | Текст |
|---|---|---|
| `skipped_exists` | — | `on_conflict=skip`, файл уже был, загрузка пропущена |
| `unscannable_accepted` | `reason` — сигнатура ClamAV (`Heuristics.Encrypted.Zip`, …) | антивирус не проверил файл целиком, принят из-за `allow_unscannable` |
| `not_media_sent_as_file` | — | просили `send_as=media`, но это не медиа — отправлен файлом |
| `photo_too_big_sent_as_file` | — | фото не влезает в лимиты фото Telegram (10 МБ, стороны) — отправлено файлом |
| `video_may_show_as_file` | `mime` | видео не mp4: Telegram может показать его файлом, а не плеером |
| `archive_file_gone` | `name` — путь внутри архива | файла уже нет в Telegram, в архив не попал |
| `archive_file_changed` | `name` | файл в Telegram изменился (другой размер), в архив не попал |
| `archive_file_undecryptable` | `name` | зашифрованный файл не расшифровался, в архив не попал |
| `archive_files_deleted` | `count` | столько выбранных файлов удалили до сборки архива |
| `other` | — | задачи, созданные до появления кодов: только `message` |

Коды ошибок задач (`error.code`) — те же, что у ошибок API (`docs/flows/*`, `src/api/exception/errors.py`).
