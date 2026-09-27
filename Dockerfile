# syntax=docker/dockerfile:1

# Сборка окружения: uv ставит зависимости строго по uv.lock; tgcrypto собирается из исходников.
FROM ghcr.io/astral-sh/uv:0.11.9-python3.13-trixie-slim AS build
ARG APT_PROXY=""
RUN if [ -n "$APT_PROXY" ]; then \
        echo "Acquire::http::Proxy \"$APT_PROXY\";" > /etc/apt/apt.conf.d/00proxy; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* /etc/apt/apt.conf.d/00proxy
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Рантайм: тот же Python, что у сборки (venv ссылается на /usr/local/bin/python3.13), без uv и
# компилятора; процесс — непривилегированный пользователь, сессия Telegram — в томе /data.
FROM python:3.13-slim-trixie
RUN groupadd --system --gid 10001 pyrobot \
    && useradd --system --uid 10001 --gid pyrobot --home-dir /app --no-create-home pyrobot \
    && mkdir -p /data \
    && chown pyrobot:pyrobot /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY app ./app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYROBOT_DATA_DIR=/data \
    PYROBOT_HTTP_PORT=8080
USER pyrobot
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-m", "app.healthcheck", "/healthz"]
CMD ["python", "-m", "app"]
