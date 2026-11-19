FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.2 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    FORWARDED_ALLOW_IPS=127.0.0.1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 appuser && \
    mkdir -p /data/session /data/tmp && \
    chown -R appuser:appuser /data

ARG UV_SYNC_ARGS="--no-default-groups"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen $UV_SYNC_ARGS --no-install-project

COPY --chmod=755 scripts/docker-entrypoint.sh /
COPY --chown=appuser:appuser . .

RUN chown appuser:appuser /app

USER appuser

EXPOSE 8000

ENTRYPOINT ["/docker-entrypoint.sh"]

CMD ["uvicorn", "--host=0.0.0.0", "--port=8000", "main:app"]
