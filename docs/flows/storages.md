# Хранилища

Хранилище — приватный канал Telegram (broadcast, без username и инвайт-ссылок). В БД — только наш UUID и `tg_chat_id`.

| Действие | Ручка | Что происходит |
|---|---|---|
| Создать | `POST /api/storages` `{name, description?}` | `CreateChannelRequest`, запись в БД, ответ с UUID |
| Список | `GET /api/storages` | id из БД + название/описание/фото из Telegram (`GetFullChannel` на канал); недоступный канал → `available: false` |
| Одно | `GET /api/storages/{id}` | плюс `files` и `total_size` |
| Изменить | `PATCH /api/storages/{id}` `{name?, description?}` | `EditTitle` / `EditChatAbout`; неизменённые значения Telegram не роняют |
| Фото | `PUT` / `GET` / `DELETE /api/storages/{id}/photo` | PUT — тело-картинка ≤ 10 МБ, проверка типа и ClamAV, не JPEG/PNG → JPEG; GET — поток из Telegram, у нас не хранится |

## Подключение из `.env`

`TELEGRAM_CHATS=-1001234567890,-1009876543210` — только id существующих каналов. При старте каждый id, которого ещё нет
в БД, проверяется (канал, без username, мы владелец или админ с правами публиковать, удалять и менять информацию)
и подключается. Не прошёл проверку — ошибка в лог, остальные подключаются. Повторный старт ничего не дублирует.
