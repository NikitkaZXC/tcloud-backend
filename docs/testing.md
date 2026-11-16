# Тестирование

| Команда | Что |
|---|---|
| `make test` | unit (`tests/unit`): пути, Range, медиа, SSRF, ClamAV-протокол, бюджет диска, параллельная выгрузка кусков, настройки |
| `make test-integration` | `tests/integration`: миграции туда-обратно и `alembic check`, гонки на уровне БД, `TaskStore` и Lua-скрипты захвата, смены приоритета и отмены на Redis, `TaskSweeper`, приоритет очереди на настоящем RabbitMQ, сквозные тесты воркеров отдельными процессами |
| `make test-api` | `tests/api`: HTTP через `httpx.AsyncClient` + `ASGITransport`, lifespan приложения и настоящие Celery-воркеры в потоках (брокер `memory://`) с фейковыми Telegram и ClamAV |
| `make test-all` / `make test-cov` | всё / с покрытием |
| `make test-docker` | всё в контейнере с ffmpeg и Postgres (`docker-compose.test.yml`) — так же, как в CI |

- Маркеры `unit`/`integration`/`api` ставятся по папке автоматически; `ffmpeg` — тесты, которым нужен ffmpeg,
  без него они пропускаются.
- Postgres: testcontainers (`postgres:18`) или готовый сервер через `TEST_POSTGRES_HOST`/`TEST_POSTGRES_PORT`
  (тогда создаётся своя временная база). Перед каждым тестом таблицы очищаются.
- Redis: testcontainers (`redis:8`) или `TEST_REDIS_HOST`; перед каждым тестом `FLUSHDB`.
  RabbitMQ: testcontainers (`rabbitmq:4-alpine`) или `TEST_RABBITMQ_URL` — только для теста приоритета.
- Воркеры (`celery.contrib.testing.worker.start_worker`, пул `threads`) поднимаются раз на сессию: download и prepare —
  по 4 потока, telegram — 2. Тестовый воркер не шлёт `worker_ready`, поэтому heartbeat фикстура запускает сама.
  На каждый тест — свой `WorkerRuntime` с фейками; после теста ждём, пока активные задачи закончатся.
- `tests/api/test_tasks.py`: отмена в очереди и во время отправки (сообщение удаляется), смена приоритета, повтор по ссылке,
  повторная доставка после падения воркера (после отправки и после коммита — без второго сообщения), дубль сообщения
  и устаревшее поколение пропускаются, вебхук при отмене из очереди.
- API-тесты идут на брокере `memory://`: он не соблюдает приоритет, а несколько встроенных воркеров на RabbitMQ в одном
  процессе делят цикл событий kombu и портят кадры AMQP (`UnexpectedFrame`). Поэтому порядок и падения проверяются
  отдельно, в `tests/integration/test_workers_e2e.py`:
  настоящие `celery worker` отдельными процессами (`tests/integration/worker_app.py`: фейковый Telegram пишет отправки
  в общий файл) на RabbitMQ, Redis и Postgres из testcontainers:
  - `high` обгоняет пять ждущих `low`; поднятый приоритет ждущей задачи двигает её вперёд;
  - воркер убит `SIGKILL` во время отправки — второй воркер получает задачу от RabbitMQ и отправляет один раз;
  - воркер убит после отправки, но до записи в `files` — второй воркер не отправляет заново, а дописывает строку
    с тем же message_id.
  Проверено мутациями: без `sent` в задаче тест падает с двумя сообщениями, без `x-max-priority` падают тесты порядка.
- `tests/fakes.py` — `FakeTelegram` (каналы и сообщения в памяти, задержка, FloodWait, сетевые сбои, счётчик
  одновременных отправок) и `FakeScanner`. `tests/factories.py` — образцы медиа (Pillow, ffmpeg lavfi).
- Гонки: 100 параллельных загрузок при 2 потоках worker-telegram, ожидание в очереди `uploads.telegram`, пауза FloodWait,
  блокировка пути в Redis, частичные обновления задачи не затирают друг друга.

## Нагрузка

```bash
make load-up        # Postgres, Redis и RabbitMQ для стенда + миграции
make load-app       # API на :8100 с фейковым Telegram
make load-workers   # три Celery-воркера отдельными процессами на настоящем RabbitMQ (LOAD_TELEGRAM_DELAY, с на файл)
make load           # locust headless: LOAD_CLIENTS, LOAD_SPAWN_RATE, LOAD_DURATION; отчёт в logs/load/
make load-down
```

Пороги: `LOAD_MAX_P95_MS` (500), `LOAD_MAX_FAIL_RATIO` (1%).
`429` от ограничителя — ожидаемое поведение, клиент стенда ждёт `retry_after`.
Замер 2026-11-16, RabbitMQ и воркеры отдельными процессами, 100 пользователей / 30 с: ~450 RPS, 0% ошибок,
`POST /api/uploads` p95 190 мс; полный путь загрузки упирается в пропускную способность Telegram
(2 потока × 0,2 с на файл), остальные ждут в очереди.
