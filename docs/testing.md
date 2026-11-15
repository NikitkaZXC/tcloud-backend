# Тестирование

| Команда | Что |
|---|---|
| `make test` | unit (`tests/unit`): пути, Range, медиа, SSRF, ClamAV-протокол, ворота и диск, параллельная выгрузка кусков, настройки |
| `make test-integration` | `tests/integration`: миграции туда-обратно и `alembic check`, гонки на уровне БД |
| `make test-api` | `tests/api`: HTTP через `httpx.AsyncClient` + `ASGITransport`, lifespan приложения с фейковыми Telegram и ClamAV |
| `make test-all` / `make test-cov` | всё / с покрытием |
| `make test-docker` | всё в контейнере с ffmpeg и Postgres (`docker-compose.test.yml`) — так же, как в CI |

- Маркеры `unit`/`integration`/`api` ставятся по папке автоматически; `ffmpeg` — тесты, которым нужен ffmpeg,
  без него они пропускаются.
- Postgres: testcontainers (`postgres:18`) или готовый сервер через `TEST_POSTGRES_HOST`/`TEST_POSTGRES_PORT`
  (тогда создаётся своя временная база). Перед каждым тестом таблицы очищаются.
- `tests/fakes.py` — `FakeTelegram` (каналы и сообщения в памяти, задержка, FloodWait, сетевые сбои, счётчик
  одновременных отправок) и `FakeScanner`. `tests/factories.py` — образцы медиа (Pillow, ffmpeg lavfi).
- Гонки: 100 параллельных загрузок при лимите 2, места в очереди, пауза FloodWait, одновременные вставки одного пути,
  атомарный захват задачи.

## Нагрузка

```bash
make load-up        # Postgres для стенда + миграции
make load-app       # приложение на :8100 с фейковым Telegram (LOAD_TELEGRAM_DELAY, с на файл)
make load           # locust headless: LOAD_CLIENTS, LOAD_SPAWN_RATE, LOAD_DURATION; отчёт в logs/load/
make load-down
```

Пороги: `LOAD_MAX_P95_MS` (500) для обычных ручек, `LOAD_MAX_BATCH_P95_MS` (1500) для пачек, `LOAD_MAX_FAIL_RATIO` (1%).
`429` от ограничителя — ожидаемое поведение, клиент стенда ждёт `retry_after`.
Замер 2026-11-15, 100 пользователей / 45 с: ~460 RPS, 0% ошибок, p95 56 мс; полный путь загрузки упирается
в пропускную способность Telegram (2 параллельные выгрузки × 0,2 с на файл).
