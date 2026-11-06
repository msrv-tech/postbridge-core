FROM node:26-slim@sha256:5adad64f9a1fca2d51972caa65983634c8a774eb300d72b20f7f2a0f60368072 AS web-build

WORKDIR /web
COPY web/package*.json /web/
RUN npm ci
COPY web /web
ARG VITE_BASE_PATH=/web/
ARG VITE_POSTBRIDGE_APP_MODE=selfhost
ARG VITE_YANDEX_METRIKA_COUNTER_ID=
ARG VITE_TELEGRAM_BOT_NAME=
ARG VITE_MAX_BOT_URL=
ARG VITE_BILLING_SUPPORT_EMAIL=
ARG VITE_POSTBRIDGE_DISABLED_PLATFORMS=
ARG VITE_POSTBRIDGE_PUBLIC_BASE_URL=https://postbridge.io
ENV VITE_BASE_PATH=${VITE_BASE_PATH}
ENV VITE_POSTBRIDGE_APP_MODE=${VITE_POSTBRIDGE_APP_MODE}
ENV VITE_YANDEX_METRIKA_COUNTER_ID=${VITE_YANDEX_METRIKA_COUNTER_ID}
ENV VITE_TELEGRAM_BOT_NAME=${VITE_TELEGRAM_BOT_NAME}
ENV VITE_MAX_BOT_URL=${VITE_MAX_BOT_URL}
ENV VITE_BILLING_SUPPORT_EMAIL=${VITE_BILLING_SUPPORT_EMAIL}
ENV VITE_POSTBRIDGE_DISABLED_PLATFORMS=${VITE_POSTBRIDGE_DISABLED_PLATFORMS}
ENV VITE_POSTBRIDGE_PUBLIC_BASE_URL=${VITE_POSTBRIDGE_PUBLIC_BASE_URL}
RUN npm run build

FROM python:3.14-slim@sha256:f1b121a95d6295f7291a0fda8280f747efd657bd055e7c1f156936ed2dc5a112 AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

ARG POSTBRIDGE_DEBIAN_MIRROR=
RUN if [ -n "$POSTBRIDGE_DEBIAN_MIRROR" ]; then \
      sed -i "s|http://deb.debian.org|${POSTBRIDGE_DEBIAN_MIRROR%/}|g" /etc/apt/sources.list.d/debian.sources; \
    fi \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core tzdata \
    && rm -rf /var/lib/apt/lists/*

# Stable dependencies and pinned model weights must not depend on application,
# frontend, or release metadata. A source-only release reuses these layers.
COPY requirements-runtime.txt /app/
RUN pip install --no-cache-dir -r requirements-runtime.txt && pip check
COPY deploy/licenses/whisper-LICENSE.txt /opt/postbridge-models/whisper-small/LICENSE
COPY src/postbridge/services/video_subtitles.py /tmp/postbridge-model-download.py
RUN python /tmp/postbridge-model-download.py --download-model && rm /tmp/postbridge-model-download.py

FROM runtime-base AS runtime

COPY pyproject.toml README.md alembic.ini /app/
COPY src /app/src
COPY alembic /app/alembic
RUN pip install --no-cache-dir --no-deps ".[video]" && pip check
COPY --from=web-build /web/dist /app/web/dist

ARG POSTBRIDGE_VERSION=0.1.0
ARG POSTBRIDGE_CONTAINER_IMAGE=ghcr.io/msrv-tech/postbridge-core
ENV POSTBRIDGE_VERSION=${POSTBRIDGE_VERSION}
ENV POSTBRIDGE_CONTAINER_IMAGE=${POSTBRIDGE_CONTAINER_IMAGE}

EXPOSE 8000

CMD ["uvicorn", "postbridge.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
