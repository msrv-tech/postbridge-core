FROM node:26-slim@sha256:5adad64f9a1fca2d51972caa65983634c8a774eb300d72b20f7f2a0f60368072 AS web-build

WORKDIR /web
COPY web/package*.json /web/
RUN npm ci
COPY web /web
ENV VITE_BASE_PATH=/web/
ENV VITE_POSTBRIDGE_APP_MODE=selfhost
ENV VITE_YANDEX_METRIKA_COUNTER_ID=
ENV VITE_TELEGRAM_BOT_NAME=
ENV VITE_MAX_BOT_URL=
ENV VITE_BILLING_SUPPORT_EMAIL=
RUN npm run build

FROM python:3.14-slim@sha256:f1b121a95d6295f7291a0fda8280f747efd657bd055e7c1f156936ed2dc5a112

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core tzdata && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements-runtime.txt requirements-ci.txt /app/
# Cache dependencies separately from application and test sources. The resolved
# requirements are identical to the dev/video extras installed below.
RUN pip install --no-cache-dir -r requirements-ci.txt

COPY README.md alembic.ini /app/
COPY src /app/src
COPY alembic /app/alembic
COPY tests /app/tests
COPY scripts /app/scripts
COPY --from=web-build /web/dist /app/web/dist

RUN pip install --no-cache-dir --no-deps ".[dev,video]" && pip check
