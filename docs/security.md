# Безопасность

## Доступ

- Пользователи с ролями `root`/`admin`/`user`, вход по логину и паролю (argon2), JWT RS256 в HttpOnly-куках с ротацией
  refresh и reuse detection, блокировка после неверных паролей, API-ключи пользователей в `X-API-Key`. Refresh-токены и
  ключи хранятся только как HMAC-SHA256 от `APP_SECRET_KEY`. Подробно — flows/users.md. Ключи JWT (`certs/`)
  читаются и разбираются один раз на процесс (`security/jwt.py`, `_private_key`/`_public_key` — объекты
  `cryptography`, не PEM-строки): подпись токена при входе и refresh — ~0,8 мс вместо ~72 мс, проверка — ~83 мкс
  вместо ~100 мкс. Новые ключи подхватываются перезапуском.
- Права проверяются на каждый запрос по БД (роль, блокировка, гранты на папки), а не по содержимому токена.
- Регистрации нет; Telegram-сессии (`/api/telegram`) доступны только root.
- Вход ограничен по IP и по логину (rate limiting, ниже) поверх блокировки аккаунта.
- Блокировка аккаунта: после `JWT_MAX_FAILED_LOGINS` (5) неверных паролей подряд вход в эту учётную запись закрыт на
  `JWT_LOCK_MINUTES` (15) мин (`429 account_locked`) — для любого IP, в том числе для владельца. Уже выданные куки и
  API-ключи продолжают работать, рестарт API блокировку не снимает. Поэтому **в prod `ROOT_LOGIN` — неочевидный логин,
  не `root`/`admin`**: знающий логин может каждые 15 мин пятью попытками снова запирать root. Снять раньше:
  сменить `ROOT_LOGIN` в `.env` и перезапустить API (root переименуется, при старте логин синхронизируется) или
  `UPDATE users SET locked_until = NULL WHERE role = 'root'`.
- Порт публикуется только на `127.0.0.1`. Postgres и ClamAV наружу не выходят.

## Rate limiting

- Redis, скользящее окно (журнал запросов): один Lua-скрипт `RATE_LIMIT_SCRIPT` (`infrastructure/cache/limits.py`,
  `RateLimitStore`) держит sorted set на ключ со временем Redis (`TIME`) каждого принятого запроса. Записи старше окна
  удаляются, отклонённые запросы не записываются, `Retry-After` — когда самая старая запись выйдет из окна. Ключ другого
  типа (например, старый счётчик) заменяется. Ключи — `{REDIS_PREFIX}:rate_limit:{scope}:{ip|user}:{id}` (redis.md).
- В коде (`src/api/dependencies/rate_limit.py`): зависимости `rate_limit_by_ip(scope, rule)` /
  `rate_limit_by_user(scope, rule)` (пользователь — `actor.user_id`, поэтому запросы по `X-API-Key` считаются на
  владельца ключа вместе с его куками) или `RateLimiter.hit` для ключей из тела (вход — по логину в нижнем регистре).
  `rate_limit_api` — общее правило `API_USER`.
- Правила — `RateLimitRule(limit, window)` в `RateLimitSettings` (`src/config/models/rate_limit.py`), переменные
  `RATE_LIMIT_<ПРАВИЛО>__LIMIT` и `RATE_LIMIT_<ПРАВИЛО>__WINDOW` (задаются парой: одно без другого — ошибка старта).
  Выключить всё: `RATE_LIMIT_ENABLED=false`.
- Превышение — `429 rate_limited`, тело `{"error": {"code": "rate_limited", "message": ..., "retry_after": N}}` и
  заголовок `Retry-After: N` (секунды, ≥ 1). Если Redis недоступен — запрос пропускается (fail open, в лог
  `Rate limit check failed`); блокировка аккаунта при этом продолжает работать.
- Лимиты эндпоинтов задаются в роутах (или при подключении роутера в `endpoints.py`), значения — `RATE_LIMIT_*`.

| Правило | По умолчанию | Ключ | Где |
|---|---|---|---|
| `SIGNIN_IP` | 20 / 60 с | IP | `POST /api/auth/signin` |
| `SIGNIN_LOGIN` | 10 / 900 с | логин | `POST /api/auth/signin` (`RateLimiter.hit` по телу) |
| `REFRESH_IP` | 60 / 60 с | IP | `POST /api/auth/refresh`, `POST /api/auth/logout` (свои счётчики) |
| `HEALTH_IP` | 120 / 60 с | IP | `GET /api/health` |
| `TELEGRAM_LOGIN_USER` | 10 / 600 с | пользователь | `POST /api/telegram/{session}/qr`, `/code`, `/sign-in`, `/password` |
| `UPLOAD_USER` | 300 / 60 с | пользователь | `POST /api/uploads`, `/uploads/from-url`, `/uploads/sessions`, каждый `PUT /uploads/sessions/{id}` (общий счётчик; 429 до чтения тела) |
| `UPLOAD_BATCH_USER` | 30 / 60 с | пользователь | `POST /api/uploads/batch` |
| `ARCHIVE_USER` | 30 / 3600 с | пользователь | `POST /api/folders/archive`, `/folders/archive/selection` |
| `PREVIEWS_BACKFILL_USER` | 3 / 3600 с | пользователь | `POST /api/files/previews/backfill` |
| `PREVIEWS_CLEAR_USER` | 3 / 3600 с | пользователь | `DELETE /api/files/previews` |
| `FILE_READ_USER` | 3000 / 60 с | пользователь | `GET /api/files/{id}/download`, `/files/{id}/thumb`, `/tasks/{id}/archive` — вместо общего правила, чтобы сетка большой папки не упиралась в 429 |
| `API_USER` | 1200 / 60 с | пользователь | все остальные ручки со входом (роутеры users, telegram, settings, storages, folders, uploads/tasks, files; `GET /api/account`; `/auth/logout/all`, `/auth/sessions*`, `/auth/password`) — поверх своих правил |

IP клиента — `request.client.host`. uvicorn берёт его из `X-Forwarded-For` только от адресов из
`FORWARDED_ALLOW_IPS` (в образе `127.0.0.1`). За nginx фронтенда в другом контейнере задайте в `.env`
`FORWARDED_ALLOW_IPS` равным IP или подсети прокси, **никогда `*`**: иначе клиент подделает IP через `X-Forwarded-For`
и обойдёт лимиты по IP. Пока не задано, все клиенты за прокси делят один IP (лимиты по IP общие на всех).

## Файлы

Файлы никогда не исполняются и не открываются ничем, кроме декодеров-валидаторов и ClamAV.
Честный предел: ClamAV ловит известное и типовое, не всё. Поэтому то, что проверить нельзя, по умолчанию отклоняется.

Принимаются медиа и любые другие файлы, в том числе исполняемые, скрипты и установщики (exe, dmg, sh, apk…):
списка запрещённых типов нет. Защита от них — в том, что не-медиа никогда не открывается в браузере на нашем адресе
(«Скачивание»), и в ClamAV. Проверки по порядку:

1. Тип определяется по magic bytes (`filetype`), расширение и `Content-Type` не учитываются.
2. Медиа проверяет декодер: Pillow (`verify` + `load`, лимит пикселей против decompression bomb),
   ffprobe в subprocess без shell, с таймаутом. Видео/аудио различаются по потокам, а не по контейнеру.
3. Расширение должно соответствовать реальному типу (`extension_mismatch`): медиа — только с медиа-расширением,
   не-медиа не может называться `.jpg`/`.mp4`/… (иначе Telegram показал бы его как фото или видео).
4. ClamAV (`clamd INSTREAM`) со строгим `docker/clamd.conf`: `AlertExceedsMax` (без неё файл, упёршийся в лимит,
   проходит непроверенным), `AlertEncrypted`, `AlertOLE2Macros`, `AlertBrokenExecutables`, `DetectPUA`, `HeuristicAlerts`,
   `MaxRecursion 16`, `MaxFiles 10000`, `MaxScanTime` 30 мин, `MaxFileSize` = потолок ClamAV 2147483645 байт,
   `MaxScanSize`/`StreamMaxLength 4000M`. Вирус, PUA, битый исполняемый → `infected`.
   Зашифрованное, Office с макросами, превышен лимит содержимого → `unscannable` (см. integrations.md).
   Файл больше `CLAMAV_MAX_FILE_SIZE` (= `MaxFileSize`) ClamAV проверить не может вовсе: он не отправляется в clamd и
   принимается с предупреждением `too_big_to_scan` даже без `allow_unscannable` (решение владельца; честный предел —
   такие файлы проходят только проверку типа и декодеры). Скачивание не-медиа остаётся sandboxed («Скачивание»). Недоступен → загрузка
   отклоняется (fail-closed), перед этим `UPLOAD_RETRIES` повторов — ClamAV может просто перезапускаться.
5. `allow_unscannable=true` в загрузке принимает `unscannable` с предупреждением в задаче. Вирусы он не пропускает:
   найденная сигнатура важнее эвристики (`HeuristicScanPrecedence no`).
6. Временные файлы: случайные имена, права `0600`, удаляются в любом исходе; имя пользователя в ФС не попадает,
   только его расширение (по нему Telethon решает, фото это или документ).

## CSRF и заголовки

- `OriginCheckMiddleware`: POST/PUT/PATCH/DELETE с заголовком `Origin`, хост которого не совпадает с `Host` запроса и
  которого нет в `APP_ALLOWED_ORIGINS`, получают `403 cross_site_request`. Запросы без `Origin` (скрипты, curl) проходят.
  Если прокси меняет `Host`, внешний адрес нужно добавить в `APP_ALLOWED_ORIGINS`. Вместе с `SameSite=lax` это закрывает
  CSRF на куках.
- `SecurityHeadersMiddleware` на всех ответах: `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
  COOP/CORP, `Permissions-Policy`; CSP `default-src 'none'` и `Cache-Control: no-store`, если маршрут не задал своих
  (кроме путей Swagger, `settings.app.docs_paths` — Swagger иначе не загрузится); HSTS при `JWT_SECURE=true`.

## CORS (фронтенд)

- Схема деплоя: фронтенд на `site.com`, бэкенд на `site.com/api`. Это один origin, CORS браузеру не нужен,
  `APP_ALLOWED_ORIGINS` пустой. Прокси отправляет на бэк `/api/*` и сохраняет `Host`. Swagger (`APP_DOCS_URL`,
  `APP_REDOC_URL`, `APP_OPENAPI_URL`, по умолчанию `/docs`, `/redoc`, `/openapi.json`) в prod-compose выключен пустыми
  значениями (пусто — маршрута нет), наружу через прокси его не публикуют (deployment.md).
- `CORSMiddleware` (в `main.py`, самый внешний, как в nextgate) настраивается `APP_ALLOWED_ORIGINS`,
  `APP_IS_ALLOWED_CREDENTIALS`, `APP_ALLOWED_METHODS`, `APP_ALLOWED_HEADERS`. Нужен для dev-сервера фронтенда на другом
  порту (`["http://localhost:3000"]`). Список пустой по умолчанию: чужой браузерный JS ответов не видит.
  Тот же список пропускает `OriginCheckMiddleware`, так что адрес фронтенда добавляется в одно место.
- Фронтенду открыты заголовки (`APP_EXPOSED_HEADERS`) `Accept-Ranges`, `Content-Disposition`, `Content-Range`, `Retry-After`, `Upload-Offset`.
- Фронтенд должен быть на том же сайте (например `app.example.com` и `api.example.com`): куки `SameSite=lax` и
  `Cross-Origin-Resource-Policy: same-site` не работают между разными сайтами. Другой сайт потребует `JWT_SAME_SITE=none`
  (только с `JWT_SECURE=true`) и смены CORP — это ослабляет защиту, решать отдельно.
- fetch с кук: `credentials: "include"`.

## Скачивание

Только скачивание: всё, что не фото, видео, аудио или GIF, браузер получает как файл и ничего не исполняет и не
показывает на адресе tcloud.

- `GET /api/files/{uuid}/download` (и `200`, и `206` по `Range`) всегда отдаёт `X-Content-Type-Options: nosniff` и
  `Content-Security-Policy: sandbox`. Не-медиа (`kind = document`, любой сохранённый MIME, в т. ч. `text/html`,
  `image/svg+xml`, `application/pdf`, `.exe`) — `Content-Type: application/octet-stream` и
  `Content-Disposition: attachment; filename*=UTF-8''…` (RFC 5987). HTML, SVG, JS, XML или PDF не откроются inline и
  не выполнят скрипт. Медиа — `inline` со своим MIME (он из декодера: только JPEG/PNG/WebP/HEIC/GIF и аудио/видео из
  списка, SVG медиа не бывает), тоже с `nosniff` и `sandbox`.
- `GET /api/tasks/{id}/archive` — `attachment`, MIME архива, `nosniff`, `Content-Security-Policy: sandbox`.
- `/thumb` и `/storages/{id}/photo` отдают только JPEG, который мы сами получили от Telegram или сделали, с `nosniff`.

## Исходящие запросы (ссылки и вебхуки)

- Только `http`/`https`. Адрес вебхука (`callback_url` и настройка `uploads.webhook_url`) проверяется ещё при приёме:
  IP-литерал непубличного адреса сразу `422 unsafe_url` (`integrations/web.check_webhook`).
- DNS резолвится один раз, **все** адреса должны быть публичными (`is_global`): нет localhost, LAN, link-local,
  CGNAT, метаданных облака, IPv4-mapped IPv6. Соединение идёт на проверенный IP, Host и SNI — исходные:
  DNS rebinding между проверкой и запросом невозможен.
- Не больше 5 редиректов, каждый проверяется заново. Прокси из окружения игнорируются.
- Таймауты, жёсткий лимит размера при чтении.
- Вебхук подписан: `X-Tcloud-Signature: sha256=HMAC(APP_SECRET_KEY, body)`.

## Контейнер и секреты

- Приложение под `appuser` (uid 1000), read-only rootfs, писать можно только в volumes `session`, `tmp` и tmpfs `/tmp`.
- `.env` и файлы сессий Telegram не коммитятся и не попадают в образ (`.dockerignore`: `.env*`, `data/`, `*.session*`);
  `make env` ставит `.env` права `0600`. Каждый файл сессии равен паролю от аккаунта: права `0600`,
  вход и выход только через `/api/telegram` и только для root, пароль 2FA не логируется и не хранится (flows/auth.md).
- loguru настроен с `diagnose=False`: в трейсбеках нет значений переменных, иначе туда попадали бы пароли и ключи.
- Неизвестный логин при входе пишется в лог только как `sha256:` + 12 hex-символов (опечатка в логине часто бывает
  паролем), у вебхука — только `scheme://host`, без пути и query (там бывают токены).
- Длина секретов проверяется при загрузке настроек: `APP_SECRET_KEY` ≥ 32 символов, `ROOT_PASSWORD` ≥ 12, иначе
  API и воркеры не стартуют (`src/config/models/app.py`, `root.py`). `make env` генерирует оба с запасом
  (43 и 24 символа). В ошибках валидации настроек значения скрыты (`hide_input_in_errors` в `src/config/base.py`).
- RabbitMQ и Redis без портов наружу и с паролями (`make env`); в Redis лежат только статусы задач, не файлы.
- Зависимости залочены в `uv.lock`, `pip-audit` — в `make check`.
- Файлы с `encrypt=false` и медиа (`send_as=media`) лежат в каналах открытыми, поэтому каналы обязаны быть приватными (без username) —
  при подключении из `.env` это проверяется.

## Шифрование файлов

Включено по умолчанию для всех загрузок с `send_as=file`; `send_as=media` без явного `encrypt` идёт открытым,
`encrypt=false` выключает (flows/uploads.md). Защищает от утечки канала или аккаунта Telegram,
но не от того, у кого есть сервер и `.env`: ключ лежит там, а проверки типа и ClamAV идут по открытому файлу.

- `APP_ENCRYPTION_KEY` — 32 байта в base64 (`make env` генерирует). Неверный формат — приложение не стартует; пусто —
  шифрование выключено (`503 encryption_unavailable`). Ключ не логируется. Потеря или смена ключа делает зашифрованные
  файлы нечитаемыми: храните копию отдельно; ротации нет. Что и как бэкапить, что теряется без каждой части —
  [deployment.md, «Бэкапы и восстановление»](deployment.md#бэкапы-и-восстановление).
- У каждого файла свой случайный ключ (DEK), он хранится только обёрнутым ключом сервера (AES-256-GCM).
- Файл: заголовок 71 байт (`TCE1`, nonce и обёрнутый DEK, 7 байт случайного префикса nonce), дальше блоки по 64 КиБ
  открытого текста, каждый — AES-256-GCM с тегом 16 байт. Nonce блока = префикс + номер блока (4 байта) + флаг
  последнего блока: перестановка, подмена и обрезка блоков обнаруживаются. Код — `infrastructure/security/encryption.py`.
- Заголовок лежит и в начале файла в Telegram, и в `files.encryption_header` — по нему файл можно расшифровать и без
  БД (будущее восстановление из Telegram).
- В Telegram имя `{uuid}.bin`, без превью и медиа-атрибутов; настоящие имя, MIME и `sha256` открытого файла — только в
  БД. `sha256` позволяет понять по БД, что два файла одинаковые (решение владельца).
- Файл больше одного сообщения Telegram (flows/uploads.md, «Большие файлы частями») шифруется так же целиком, одним
  DEK и одним потоком, а при отправке режется **по границам блоков** (64 КиБ + 16 байт тега): часть 0 — заголовок и
  первые блоки, следующие — только блоки, в Telegram `{uuid}.part{N}.bin`. Nonce блока содержит его номер во всём
  файле, поэтому любую часть можно расшифровать отдельно по заголовку из БД и её `offset` (так работает `Range`), а
  перестановка частей ломает теги. Обрезка части ловится сверкой её размера с `file_parts.size`, конец файла — флагом
  последнего блока.
- Старые файлы не перешифровываются.
- Превью зашифрованных фото, GIF и видео (≤ 320 px JPEG) делается на сервере из открытого файла и хранится только в БД
  (`files.thumbnail`): `nonce (12 байт) + AES-256-GCM` тем же DEK файла со случайным nonce и AAD `TCE1-thumbnail`
  (не пересекается с блоками тела). В Telegram превью не уходит; без ключа сервера его не открыть.
