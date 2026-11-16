# Интеграции

## Telegram (Telethon)

Пользовательский аккаунт, а не бот: бот не может создавать каналы и не имеет Premium.
Три сессии одного аккаунта в volume `session`: `TELEGRAM_SESSION` (API: каналы, удаление, события),
`TELEGRAM_WORKER_SESSION` (worker-telegram: выгрузка) и `TELEGRAM_DOWNLOAD_SESSION` (API: отдача файлов). Один
SQLite-файл сессии нельзя открывать из двух процессов — будут блокировки и риск `AUTH_KEY_DUPLICATED`; скачивания
вынесены в свою сессию, чтобы их трафик и FloodWait не задевали управление каналами. В каждую входят отдельно через
`/api/auth` (в Telegram это три устройства, `tcloud-api`, `tcloud-worker`, `tcloud-download`; flows/auth.md).
Обновления получает только API-сессия — ради событий об удалённых сообщениях (flows/folders-and-files.md).

| Лимит | Значение | Как учитываем |
|---|---|---|
| Файл | 2 ГБ, с Premium 4 ГБ (4000/8000 кусков по 512 КБ) | проверка до загрузки и во время приёма; Premium кешируется на `UPLOAD_PREMIUM_TTL` |
| Фото как фото | 10 МБ, сумма сторон ≤ 10000, соотношение ≤ 1:20 | не влезло → отправляем документом с предупреждением |
| Фото как фото — формат | только JPEG/PNG | webp/heic/gif переводим в JPEG (Telegram всё равно сожмёт) |
| Подпись | 1024 символа | в подписи только `#tc<hex>`; одно слово без дефисов — поиск Telegram находит его целиком |
| Превью видео | JPEG ≤ 200 КБ, ≤ 320 px | ffmpeg, качество снижается, пока не влезет |
| Название / описание канала | 128 / 255 | валидация запроса |
| Каналов и супергрупп на аккаунт | 500 / 1000 с Premium (`CHANNELS_MAX*`) | Telegram `CHANNELS_TOO_MUCH` при создании хранилища → `409 channels_limit` с `limit`; считаются все каналы и группы аккаунта, не только хранилища, поэтому заранее не проверяем |
| Удаление сообщений | 100 за запрос | пачками |
| FloodWait | — | ≤ 60 с Telethon пережидает сам; дольше — пауза всей выгрузки (см. background-tasks.md) |

- Видео не перекодируем: Telegram не пережимает видео, загруженное через API. Размеры, длительность и поворот берём
  из ffprobe и передаём в `DocumentAttributeVideo` — без них соотношение сторон «плывёт». Плеер надёжно работает
  с MP4; другие контейнеры могут показаться файлом — в ответе будет предупреждение.
- Куски файлов > 10 МБ отправляются параллельно (`TELEGRAM_UPLOAD_WORKERS`, `SaveBigFilePart`): Telethon шлёт их по одному
  и упирается в задержку сети. Плюс `cryptg` для быстрого AES.
- Скачивание: смещение запроса к Telegram должно быть кратно 512 КБ, лишние байты в начале отрезаем — так работает `Range`.
- Если `access_hash` канала ещё нет в сессии, список диалогов загружается один раз.
- Исключения Telethon переводятся в `TelegramFloodWaitError`, `TelegramUnavailableError`, `TelegramUnauthorizedError`
  (401: сессия не вошла или отозвана), ошибки входа (`invalid_code`, `invalid_password`, `invalid_phone`,
  `password_needed`, `session_too_fresh`) и `TelegramError` для остального.
- Вход (`TelegramLogin`): QR (`auth.exportLoginToken`, ждёт `UpdateLoginToken`, поэтому клиент входа получает обновления)
  или код (`auth.sendCode` / `auth.signIn`), 2FA через SRP. Старые сессии: `account.getAuthorizations` /
  `account.resetAuthorization`, который Telegram разрешает только сессиям старше 24 ч.

## ClamAV

Контейнер `clamav/clamav:*-debian` (alpine-теги собраны только под amd64). `docker/clamd.conf` поднимает лимиты
потока до 4000 МБ (по умолчанию clamd режет на 100 МБ) и включает строгие проверки, см. security.md.
Первый старт качает базы несколько минут.

Ответы `clamd` делятся на два класса:

| Ответ | Код | Что значит |
|---|---|---|
| `Heuristics.Encrypted.*` | `unscannable` | зашифрованный архив, PDF или документ: внутрь не заглянуть |
| `Heuristics.Limits.Exceeded.*` | `unscannable` | упёрлись в лимит (размер, число файлов, вложенность, время) — проверено не всё |
| `Heuristics.OLE2.ContainsMacros*` | `unscannable` | Office с макросами |
| любой другой `FOUND` (сигнатуры, `PUA.*`, `Heuristics.Broken.*`, фишинг) | `infected` | отклоняется всегда |

`HeuristicScanPrecedence no`: эвристика сообщается только в конце, а найденная сигнатура важнее. Поэтому
`allow_unscannable` не пропустит вирус, лежащий рядом с зашифрованным архивом: проверено на `clamav 1.5.4` —
zip с EICAR и зашифрованным zip внутри даёт `Eicar-Test-Signature`, а не `Heuristics.Encrypted.Zip`.
Опции `AllMatchScan` в `clamd.conf` нет (это флаг `clamscan`).

## Исходящий HTTP

`src/infrastructure/integrations/web` — httpx с проверкой адресов (см. security.md), используется для загрузки по ссылке и вебхуков.
