# Безопасность

## Доступ

- Один ключ `APP_API_KEY` в заголовке `X-API-Key`, сравнение в постоянном времени (`secrets.compare_digest`).
- Порт публикуется только на `127.0.0.1`. Postgres и ClamAV наружу не выходят.

## Файлы

Файлы никогда не исполняются и не открываются ничем, кроме декодеров-валидаторов и ClamAV.
Честный предел: ClamAV ловит известное и типовое, не всё. Поэтому то, что проверить нельзя, по умолчанию отклоняется.

Принимаются медиа и любые другие файлы, кроме исполняемых. Проверки по порядку:

1. Имя проверяется до чтения тела: исполняемые расширения (`BLOCKED_EXTENSIONS` в `media/analyze.py`: exe, dll, msi,
   bat, cmd, ps1, vbs, js, hta, lnk, jar, apk, app, pkg, dmg, sh, deb, rpm, AppImage, iso, img, vhd и др.) →
   `415 blocked_file_type`. Смотрится последнее расширение, без хвостовых точек и пробелов (`a.exe.` — тоже exe),
   регистр не важен.
2. Тип определяется по magic bytes (`filetype`), расширение и `Content-Type` не учитываются. Исполняемое содержимое
   (PE/MZ, ELF, Mach-O, Java class, DEX, `#!`-скрипты, deb, rpm, cab, crx, swf, wasm) → `blocked_file_type` при любом имени.
3. Медиа проверяет декодер: Pillow (`verify` + `load`, лимит пикселей против decompression bomb),
   ffprobe в subprocess без shell, с таймаутом. Видео/аудио различаются по потокам, а не по контейнеру.
4. Расширение должно соответствовать реальному типу (`extension_mismatch`): медиа — только с медиа-расширением,
   не-медиа не может называться `.jpg`/`.mp4`/… (иначе Telegram показал бы его как фото или видео).
5. Zip (и всё на нём: docx, xlsx, odt, epub, jar, apk) — читается список файлов, без распаковки. Исполняемое имя в
   любом сегменте пути (`bin/setup.exe`, `Evil.app/Contents/…`) → `blocked_file_type`. Вложенные архивы, tar, 7z, rar
   так не разбираются — их проверяет ClamAV.
6. ClamAV (`clamd INSTREAM`) со строгим `docker/clamd.conf`: `AlertExceedsMax` (без неё файл, упёршийся в лимит,
   проходит непроверенным), `AlertEncrypted`, `AlertOLE2Macros`, `AlertBrokenExecutables`, `DetectPUA`, `HeuristicAlerts`,
   `MaxRecursion 16`, `MaxFiles 10000`, `MaxScanTime` 15 мин. Вирус, PUA, битый исполняемый → `infected`.
   Зашифрованное, Office с макросами, превышен лимит → `unscannable` (см. integrations.md). Недоступен → загрузка
   отклоняется (fail-closed), перед этим `UPLOAD_RETRIES` повторов — ClamAV может просто перезапускаться.
7. `allow_unscannable=true` в загрузке принимает `unscannable` с предупреждением в задаче. Вирусы он не пропускает:
   найденная сигнатура важнее эвристики (`HeuristicScanPrecedence no`).
6. Временные файлы: случайные имена, права `0600`, удаляются в любом исходе; имя пользователя в ФС не попадает,
   только его расширение (по нему Telethon решает, фото это или документ).

## Скачивание

`GET /api/files/{uuid}/download` всегда отдаёт `X-Content-Type-Options: nosniff`. Не-медиа (`kind = document`) —
`application/octet-stream` и `Content-Disposition: attachment`: HTML, SVG или PDF не откроются в браузере
на нашем адресе и не выполнят скрипт.

## Исходящие запросы (ссылки и вебхуки)

- Только `http`/`https`.
- DNS резолвится один раз, **все** адреса должны быть публичными (`is_global`): нет localhost, LAN, link-local,
  CGNAT, метаданных облака, IPv4-mapped IPv6. Соединение идёт на проверенный IP, Host и SNI — исходные:
  DNS rebinding между проверкой и запросом невозможен.
- Не больше 5 редиректов, каждый проверяется заново. Прокси из окружения игнорируются.
- Таймауты, жёсткий лимит размера при чтении; исполняемое содержимое обрывается на первых 8 КБ.
- Вебхук подписан: `X-Tcloud-Signature: sha256=HMAC(APP_API_KEY, body)`.

## Контейнер и секреты

- Приложение под `appuser` (uid 1000), read-only rootfs, писать можно только в volumes `session`, `tmp` и tmpfs `/tmp`.
- `.env` и файлы сессий Telegram не коммитятся. Каждый файл сессии равен паролю от аккаунта.
- RabbitMQ и Redis без портов наружу и с паролями (`make env`); в Redis лежат только статусы задач, не файлы.
- Зависимости залочены в `uv.lock`, `pip-audit` — в `make check`.
- Данные в каналах пока не зашифрованы, поэтому каналы обязаны быть приватными (без username) —
  при подключении из `.env` это проверяется.
