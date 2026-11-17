# Хранилища

Хранилище — приватный канал Telegram (broadcast, без username и инвайт-ссылок). В БД — только наш UUID и `tg_chat_id`.

| Действие | Ручка | Что происходит |
|---|---|---|
| Создать | `POST /api/storages` `{name, description?}` | `CreateChannelRequest`, запись в БД, ответ с UUID. Аккаунт уже в 500 (1000 с Premium) каналах и группах → `409 channels_limit` |
| Список | `GET /api/storages` | id из БД + название/описание/фото из Telegram (`GetFullChannel` на канал); недоступный канал → `available: false` |
| Одно | `GET /api/storages/{id}` | плюс `files` и `total_size` |
| Изменить | `PATCH /api/storages/{id}` `{name?, description?}` | `EditTitle` / `EditChatAbout`; неизменённые значения Telegram не роняют |
| Удалить | `DELETE /api/storages/{id}?keep_channel=false` | `204`. Записи файлов и папок удаляются из БД (каскад), канал — `DeleteChannelRequest` вместе со всеми сообщениями, безвозвратно. `keep_channel=true` — канал с файлами остаётся в Telegram, отвязывается только БД. Идут загрузки в хранилище — `409 storage_busy` (дождаться или отменить). Канал уже удалён руками — удаление всё равно проходит |
| Фото | `PUT` / `GET` / `DELETE /api/storages/{id}/photo` | PUT — тело-картинка ≤ 10 МБ, проверка типа и ClamAV, не JPEG/PNG → JPEG; GET — поток из Telegram, у нас не хранится |

## Сколько каналов ещё можно создать

`GET /api/account` → `{premium, file_limit, channels_used, channels_limit}`. `channels_used` — сколько каналов и
супергрупп у аккаунта API-сессии (все диалоги, включая архив, где сущность — `Channel`; это то, что Telegram считает
в `CHANNELS_TOO_MUCH`; обычные группы туда не входят), `channels_limit` — 500 или 1000 с Premium. Подсчёт — один проход
по диалогам (`iter_dialogs`), он медленный, поэтому результат кешируется в памяти API на `UPLOAD_CHANNELS_TTL`
(300 с). Telegram не ответил (флуд, сеть, сессия отозвана) → `channels_used: null`, остальные поля как обычно, ошибка
не кешируется. `premium` по-прежнему читается из Telegram, поэтому без входа в API-сессию ручка отвечает
`503 telegram_unauthorized`, как и раньше.

## Подключение из `.env`

`TELEGRAM_CHATS=-1001234567890,-1009876543210` — только id существующих каналов. При старте каждый id, которого ещё нет
в БД, проверяется (канал, без username, мы владелец или админ с правами публиковать, удалять и менять информацию)
и подключается. Не прошёл проверку — ошибка в лог, остальные подключаются. Повторный старт ничего не дублирует. Удалённое через API хранилище, чей id остался в `TELEGRAM_CHATS`, при следующем
старте подключится снова (если канал оставлен `keep_channel=true`) — уберите id из `.env`.
