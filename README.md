# tcloud

Файловое хранилище поверх Telegram с REST API: фото, видео, аудио, GIF и документы уходят в приватные каналы (хранилища)
через Telethon, у каждого файла свой UUID и путь `tcloud/{хранилище}/{папки}/{файл}`.

- Лимит файла по аккаунту: 2 ГБ, с Premium 4 ГБ. Отправка оригиналом или сжатым медиа.
- Загрузка с устройства одним запросом, по ссылке и пачками до 100 ссылок; прогресс, номер в очереди, вебхуки.
- Очереди Celery на RabbitMQ с приоритетом `low/normal/high`: скачивание, проверка и отправка в Telegram — отдельные
  воркеры, статусы в Redis. Отмена, смена приоритета, повтор, без дублей при падении воркера, Flower и health по воркерам.
- Проверка типа по содержимому, запрет исполняемых файлов (и внутри zip), строгий ClamAV, защита от SSRF.
- Архивы папки или выбранных файлов и папок (zip, 7z, tar.gz/bz2/xz) фоновой задачей.
- Поиск файлов по имени (без учёта регистра, кириллица; сортировка по имени на языке интерфейса, `lang=ru|en`) и фильтрам: тип, MIME, размер, дата, шифрование; в папке, хранилище или везде (`GET /api/files/search`).
- Карта активности загрузок по дням (`GET /api/files/activity`).
- Настройки пользователя на сервере (`GET/PATCH/DELETE /api/account/settings`): значения по умолчанию для загрузок и архивов действуют на любом устройстве и для API-ключей, плюс настройки интерфейса и папка чатов «TCloud» в Telegram для всех каналов хранилищ ([docs/flows/settings.md](docs/flows/settings.md)).
- Размеры и число файлов папок в листинге, число каналов аккаунта в `GET /api/account`.
- Индикаторы чтения и записи по хранилищам для всех клиентов сразу (`GET /api/storages/activity`, метки в Redis на 5 с).
- Превью фото и видео (`GET /api/files/{id}/thumb`, JPEG ≤ 320 px, с `ETag`): из Telegram, у зашифрованных файлов — своё, зашифрованное в БД.
- Шифрование по умолчанию (AES-256-GCM, ключ сервера `APP_ENCRYPTION_KEY`) для всех файлов, кроме `send_as=media`; `encrypt=false` выключает. В Telegram только шифротекст.
- Вход в Telegram через API: QR или код, двухэтапная проверка, старые сессии tcloud отзываются сами.
- Удалили файл руками в Telegram — запись в БД пропадает сама (события Telethon, плюс проверка при скачивании).
- FastAPI, PostgreSQL, RabbitMQ, Redis, Celery, Python 3.14 (uv), всё в Docker.

## Быстрый старт

```bash
make env          # .env + сгенерированные ключи; заполнить TELEGRAM_API_ID / TELEGRAM_API_HASH
make up           # API на 127.0.0.1:8000, Swagger — /docs
```

Сначала войдите как root (`ROOT_LOGIN` / `ROOT_PASSWORD` из `.env`): `POST /api/auth/signin`, токены придут в HttpOnly-куках.
Потом войдите в Telegram (Swagger, раздел **Telegram**, по QR или по коду с 2FA) отдельно для сессий `api`, `upload` и `download`:
`POST /api/telegram/api/qr`, `POST /api/telegram/upload/qr`, `POST /api/telegram/download/qr` ([docs/flows/auth.md](docs/flows/auth.md)).

Пользователи, роли и гранты на папки описаны в [docs/flows/users.md](docs/flows/users.md). Для скриптов удобнее API-ключ:
`POST /api/users/{id}/api-keys`.

```bash
curl -H "X-API-Key: $TCLOUD_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"Фото"}' http://127.0.0.1:8000/api/storages
curl -H "X-API-Key: $TCLOUD_KEY" --data-binary @photo.jpg \
  "http://127.0.0.1:8000/api/uploads?path=tcloud/<uuid>/отпуск/photo.jpg&priority=high"
```

## Документация

Всё подробно — в [`docs/`](docs/README.md): архитектура, база, безопасность, Telegram и его лимиты, фоновая обработка,
разработка, развёртывание, тесты и процессы.

## Тесты

```bash
make test-all       # локально (Postgres, Redis, RabbitMQ через testcontainers)
make test-docker    # всё в Docker, с ffmpeg
make load-up && make load-app & make load   # нагрузка (locust)
```
