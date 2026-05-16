FROM node:20-slim AS web-build

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

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md alembic.ini /app/
COPY src /app/src
COPY alembic /app/alembic
COPY tests /app/tests
COPY scripts /app/scripts
COPY --from=web-build /web/dist /app/web/dist

RUN pip install --no-cache-dir ".[dev]"
