# TCloud

Облачное хранилище медиа поверх Telegram с REST API. Файлы уходят в приватные каналы
(**хранилища**) через Telethon, у каждого файла свой UUID и путь
`tcloud/{uuid хранилища}/{папки}/{файл}`.

- Только медиа: фото, видео, аудио, GIF. Отправка файлом-оригиналом или сжатым медиа (`send_as`).
- Лимит файла определяется по аккаунту: 2 ГБ, с Premium 4 ГБ.
- Загрузка с устройства или по ссылке, прогресс через пулинг (`GET /tasks/{id}`) или вебхук.
- Проверка типа по содержимому, ClamAV, защита от SSRF.
- PostgreSQL, всё в Docker Compose.

Полное ТЗ — [`docs/TZ.md`](docs/TZ.md), устройство кода — [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Запуск

```bash
cp .env.example .env
docker compose run --rm --no-deps app python -m tcloud genkey   # → TCLOUD_API_KEY и TCLOUD_DB_PASSWORD в .env
# заполнить TCLOUD_TG_API_ID / TCLOUD_TG_API_HASH (https://my.telegram.org)
docker compose run --rm --no-deps app python -m tcloud login    # один раз: телефон, код, 2FA
docker compose up -d
```

API слушает только `127.0.0.1:8000`, Swagger — `http://127.0.0.1:8000/docs`.
Первый запуск ClamAV качает базы несколько минут — до этого загрузки отклоняются с `503 scanner_unavailable`.

Нужен **пользовательский** аккаунт Telegram (бот не может создавать каналы). Файл сессии лежит в volume
`session` и равен паролю от аккаунта.

## Пример

```bash
H="X-API-Key: $TCLOUD_API_KEY"; API=http://127.0.0.1:8000

# хранилище = приватный канал
SID=$(curl -s -H "$H" -H 'Content-Type: application/json' -d '{"name":"Фото"}' $API/storages | jq -r .id)

# загрузка в два шага: задача → тело файла
TASK=$(curl -s -H "$H" -H 'Content-Type: application/json' \
  -d "{\"path\":\"tcloud/$SID/отпуск/photo.jpg\",\"size\":$(stat -f%z photo.jpg),\"send_as\":\"media\"}" \
  $API/uploads | jq -r .id)
curl -s -H "$H" -T photo.jpg $API/uploads/$TASK
curl -s -H "$H" $API/tasks/$TASK          # stage, progress, speed, eta, file

# по ссылке, со своим именем
curl -s -H "$H" -H 'Content-Type: application/json' \
  -d "{\"url\":\"https://example.com/IMG_1.JPG\",\"path\":\"tcloud/$SID/море.jpg\"}" $API/uploads/from-url

curl -s -H "$H" "$API/folders?path=tcloud/$SID/отпуск"     # содержимое папки
curl -s -H "$H" -r 0-1023 $API/files/<uuid>/download        # скачивание с Range
```

## API

| Метод | Путь | Что делает |
|---|---|---|
| `GET` | `/health`, `/account` | состояние БД/TG/ClamAV; Premium и лимит файла |
| `POST` `GET` | `/storages` | создать / список хранилищ |
| `GET` `PATCH` | `/storages/{id}` | хранилище (+ число файлов и объём) / сменить название и описание |
| `PUT` `GET` `DELETE` | `/storages/{id}/photo` | фото канала |
| `POST` `GET` `DELETE` | `/folders` | создать / содержимое (`?path=`) / удалить рекурсивно |
| `POST` + `PUT` | `/uploads` → `/uploads/{task}` | загрузка с устройства |
| `POST` | `/uploads/from-url` | загрузка по ссылке |
| `GET` | `/tasks/{id}` | прогресс |
| `GET` | `/files/{uuid}`, `/files/by-path?path=` | метаданные файла |
| `GET` | `/files/{uuid}/download` | скачать потоком из TG, `Range` поддерживается |
| `DELETE` | `/files/{uuid}` | удалить из TG и БД |

Ошибки: `{"error": {"code": "...", "message": "..."}}`.

## Тесты

```bash
docker compose --profile test run --rm tests
```

Telegram и ClamAV в тестах фейковые, Postgres и ffmpeg — настоящие.
