FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home tcloud \
    && mkdir -p /data/session /data/tmp && chown -R tcloud:tcloud /data

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
ARG DEV=0
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$DEV" = "1" ]; then pip install --no-cache-dir -r requirements-dev.txt; fi
COPY . .

USER tcloud
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m tcloud serve"]
