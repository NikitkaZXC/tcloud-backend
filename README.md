# TCloud

Telegram как облачное хранилище с REST API. Файлы шифруются (AES-256-GCM) до отправки,
большие файлы режутся на части по ~1.9 ГБ, при скачивании части собираются обратно
и расшифровываются на лету.

## Запуск

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m tcloud genkey        # ключи → в .env
cp .env.example .env                     # заполнить
.venv/bin/python -m tcloud serve
```

Нужен **приватный канал**, куда бот добавлен админом (с правом удалять сообщения).
Вместо бота можно аккаунт: `python -m tcloud login --api-id ... --api-hash ...` выдаст session string.

## API

Все запросы с заголовком `X-API-Key`. Документация Swagger: `http://127.0.0.1:8000/docs`.

| Метод | Путь | Что делает |
|---|---|---|
| `POST` | `/files` (multipart `file`, `?wait=true`) | загрузить; без `wait` вернёт 202 и будет грузить в фоне |
| `GET` | `/files` | список |
| `GET` | `/files/{id}` | статус (`uploading` / `ready` / `failed`) и `progress` |
| `GET` | `/files/{id}/download` | скачать исходный файл |
| `DELETE` | `/files/{id}` | удалить из Telegram и из базы |

```bash
curl -H "X-API-Key: $KEY" -F file=@movie.mkv http://127.0.0.1:8000/files
curl -H "X-API-Key: $KEY" -OJ http://127.0.0.1:8000/files/<id>/download
```

## Как устроено

- Отправка через MTProto (Pyrogram), как в AnimehaTube-bot: Bot API ограничен 50 МБ.
- Файл → части по `PART_SIZE_MB` → каждая часть = заголовок + сегменты по 4 МиБ, каждый сегмент
  зашифрован AES-256-GCM. Ключ файла выводится из мастер-ключа и случайной соли (HKDF).
- Номер части/сегмента и флаг «последний» входят в проверку подлинности: подмена, перестановка
  или обрезка частей обнаруживаются при скачивании. Плюс финальная сверка SHA-256.
- В Telegram видны только имена вида `<id>.0000.tcloud`; настоящие имена — в локальной SQLite (`data/tcloud.db`).
- Храним `message_id`, а не `file_id`: `file_id` привязан к аккаунту, а его `file_reference` устаревает.
  При скачивании сообщение запрашивается заново.
- При загрузке на диске нужно место: исходный файл + одна зашифрованная часть.

## Структура

```
tcloud/
  __init__.py   версия и публичные классы
  __main__.py   CLI: serve, genkey, login
  api.py        FastAPI-эндпоинты
  config.py     настройки из .env (префикс TCLOUD_)
  crypto.py     формат частей и AES-256-GCM
  db.py         SQLite: файлы и их части
  service.py    нарезка, загрузка, сборка, удаление
  telegram.py   Pyrogram: отправка, скачивание, удаление сообщений
tests/          тесты на фейковом Telegram
```

## Тесты

```bash
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest -q
```
