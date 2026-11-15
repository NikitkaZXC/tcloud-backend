# Устройство TCloud

## Модули

```
tcloud/
  __main__.py   CLI: serve, login (вход в Telegram), genkey
  api.py        FastAPI: ручки, X-API-Key, единый формат ошибок, Range
  service.py    бизнес-логика: хранилища, папки, загрузки, задачи, вебхуки
  telegram.py   Telethon: каналы, отправка с атрибутами, стрим, удаление пачками
  media.py      тип по magic bytes, проверка Pillow/ffprobe, превью, JPEG для фото
  security.py   ClamAV (INSTREAM), SSRF-безопасные запросы, скачивание по ссылке
  paths.py      разбор и проверка путей tcloud/{storage}/...
  limits.py     все лимиты Telegram
  db.py         модели SQLAlchemy (схема — ТЗ §10)
  errors.py     TCloudError и перевод исключений в HTTP-ошибки
migrations/     Alembic; применяется при старте контейнера
docker/         clamd.conf
tests/          фейковые Telegram и ClamAV, реальные Postgres и ffmpeg
```

## Загрузка файла

```
POST /uploads ──► проверки пути, расширения, лимита, конфликта ──► task: pending
PUT /uploads/{id} ──► pending→running (атомарно) ──► приём во временный файл с лимитом  [receiving]
   └─► фоновая задача:
        magic bytes + Pillow/ffprobe ──► sha256 ──► ClamAV                              [scanning]
        send_as=media: фото не влезло в лимиты → файлом; webp/heic → JPEG; превью видео
        повторная проверка конфликта ──► send_file в TG (не больше TG_CONCURRENCY разом)  [uploading_tg]
        запись в БД (ошибка → сообщение в TG удаляется) ──► replace: старое сообщение удаляется
        временные файлы удаляются в любом исходе                                         [done | failed]
```

По ссылке — то же, только приём заменён скачиванием (`security.download`), которое обрывается на
первых байтах, если это не медиа.

## Решения

- **UUID в подписи** — `#tc<hex>` одним словом: поиск Telegram находит его целиком.
- **file_id не храним** — только `chat_id` + `message_id`, медиа запрашивается заново при скачивании.
- **Прогресс в памяти** — в БД только стадии и итог; после рестарта незавершённые задачи → `failed: interrupted`.
- **Fail-closed** — ClamAV недоступен → загрузка отклоняется.
- **SSRF** — DNS резолвится один раз, все адреса должны быть публичными, соединение идёт на проверенный IP
  (SNI и Host — исходные), каждый редирект проверяется заново. То же для `callback_url`.
- **Вебхук** — POST с телом задачи, подпись `X-TCloud-Signature: sha256=<HMAC(api_key, body)>`, 3 повтора.
