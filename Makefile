.PHONY: install install-test env app worker-download worker-prepare worker-telegram flower check format test test-integration test-api test-all test-cov test-docker dev-up dev-up-all dev-down up down logs load-up load-app load-workers load load-ui load-down

install:
	uv sync

install-test:
	uv sync --no-default-groups --group test

env:
	sh ./scripts/env-secrets.sh

app:
	uv run uvicorn --host=127.0.0.1 --port=8000 main:app --reload

CELERY = uv run celery --app src.infrastructure.background.celery.app worker --pool threads --loglevel=INFO

worker-download:
	$(CELERY) --concurrency 4 --queues uploads.download --hostname download@%h

worker-prepare:
	$(CELERY) --concurrency 4 --queues uploads.prepare --hostname prepare@%h

worker-telegram:
	$(CELERY) --concurrency 2 --queues uploads.telegram,archives.build --hostname telegram@%h

flower:
	uv run celery --app src.infrastructure.background.celery.app flower --port=5555

format:
	uv run black . && uv run isort .

check:
	uv run pip-audit
	uv run pytest tests/integration/test_migrations.py -q

test:
	uv run pytest -m unit

test-integration:
	uv run pytest -m integration

test-api:
	uv run pytest -m api

test-all:
	uv run pytest

test-cov:
	uv run pytest --cov --cov-report=term-missing

TEST_COMPOSE = docker compose -f docker-compose.test.yml

test-docker:
	$(TEST_COMPOSE) run --rm --build tests; status=$$?; $(TEST_COMPOSE) down -v; exit $$status

DEV_COMPOSE = docker compose -f docker-compose.dev.yml

dev-up:
	$(DEV_COMPOSE) up -d --wait postgres rabbitmq redis

dev-up-all:
	$(DEV_COMPOSE) --profile app up -d --build

dev-down:
	$(DEV_COMPOSE) --profile app down

PROD_COMPOSE = docker compose -f docker-compose.prod.yml

up:
	$(PROD_COMPOSE) up -d --build

down:
	$(PROD_COMPOSE) down

logs:
	$(PROD_COMPOSE) logs -f app worker-download worker-prepare worker-telegram

LOAD_ENV = tests/load/env.load
LOAD_COMPOSE = docker compose -f tests/load/docker-compose.yml
LOAD_HOST ?= http://127.0.0.1:8100
LOAD_CLIENTS ?= 100
LOAD_SPAWN_RATE ?= 20
LOAD_DURATION ?= 1m

load-up:
	$(LOAD_COMPOSE) up -d --wait
	mkdir -p logs/load/tmp
	[ -f logs/load/jwt-private.pem ] || (openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out logs/load/jwt-private.pem && openssl rsa -in logs/load/jwt-private.pem -pubout -out logs/load/jwt-public.pem)
	uv run --env-file $(LOAD_ENV) alembic upgrade head

load-app:
	uv run --env-file $(LOAD_ENV) uvicorn tests.load.app:app --port 8100 --no-access-log

LOAD_CELERY = uv run --env-file $(LOAD_ENV) celery --app tests.load.worker worker --pool threads --loglevel=WARNING

load-workers:
	$(LOAD_CELERY) --concurrency 4 --queues uploads.download --hostname download@load & \
	$(LOAD_CELERY) --concurrency 4 --queues uploads.prepare --hostname prepare@load & \
	$(LOAD_CELERY) --concurrency 2 --queues uploads.telegram,archives.build --hostname telegram@load & \
	wait

load:
	mkdir -p logs/load
	uv run locust -f tests/load/locustfile.py --headless --host $(LOAD_HOST) -u $(LOAD_CLIENTS) -r $(LOAD_SPAWN_RATE) -t $(LOAD_DURATION) --csv logs/load/report --html logs/load/report.html

load-ui:
	uv run locust -f tests/load/locustfile.py --host $(LOAD_HOST)

load-down:
	$(LOAD_COMPOSE) down -v
