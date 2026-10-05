# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatomic1 \
        libcups2 \
        libdbus-1-3 \
        libdbus-glib-1-2 \
        dbus-x11 \
        libdrm2 \
        libgbm1 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libx11-xcb1 \
        libxcb-shm0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libxshmfence1 \
        libxt6 \
        xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --home-dir /home/app --shell /usr/sbin/nologin app

COPY pyproject.toml README.md requirements-captcha.txt ./
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
COPY docker-entrypoint.sh /docker-entrypoint.sh

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir . \
    && pip install --no-cache-dir --no-deps -r requirements-captcha.txt \
    && mkdir -p /home/app/.cache/scout-api/camoufox-profiles/default \
    && chmod +x /docker-entrypoint.sh \
    && chown -R app:app /app /home/app

# Recent Camoufox fetch also installs fpgen's model in its package data.
# Keep only that data directory writable by the existing non-root build user.
RUN if [ -d /usr/local/lib/python3.12/site-packages/fpgen ]; then \
        mkdir -p /usr/local/lib/python3.12/site-packages/fpgen/data \
        && chown -R app:app /usr/local/lib/python3.12/site-packages/fpgen/data; \
    fi

USER app

RUN python -m camoufox fetch

USER root

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD runuser -u app -- python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health', timeout=3)"

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "scout_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
