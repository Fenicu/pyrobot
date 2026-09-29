# syntax=docker/dockerfile:1

# Сборка окружения: версии и хеши — строго из uv.lock, пакеты — из PYPI_INDEX (по умолчанию PyPI;
# uv sync --frozen качал бы строго по адресам файлов в uv.lock, мимо зеркала, поэтому — экспорт с
# хешами); tgcrypto собирается из исходников.
# Базовые образы — по digest: пересборка того же коммита даёт тот же рантайм (обновление — README
# «Образ»).
FROM ghcr.io/astral-sh/uv:0.11.9-python3.13-trixie-slim@sha256:c77724b2edaed795cfd1614ce68d689f7b488801190e4934d7860a00b720871c AS build
ARG APT_PROXY=""
ARG PYPI_INDEX=https://pypi.org/simple
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
    uv export --frozen --no-dev --no-emit-project --no-header -q -o /tmp/requirements.txt \
    && uv venv --no-config /app/.venv \
    && uv pip sync --no-config --python /app/.venv/bin/python --default-index "$PYPI_INDEX" \
        --require-hashes /tmp/requirements.txt

# Админка: SvelteKit собирается в статику строго по package-lock.json; пакеты — из NPM_REGISTRY (по
# умолчанию npmjs, свой реестр npm подставит вместо registry.npmjs.org из lock-файла). TS-типы API —
# закоммиченный schema.d.ts, openapi.json в контекст не входит.
FROM node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS admin
ARG NPM_REGISTRY=https://registry.npmjs.org/
WORKDIR /admin
COPY admin/package.json admin/package-lock.json admin/.npmrc ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund --registry "$NPM_REGISTRY"
COPY admin/ ./
RUN npm run build

# Рантайм: тот же Python, что у сборки (venv ссылается на /usr/local/bin/python3.13), без uv и
# компилятора; процесс — непривилегированный пользователь, сессия Telegram — в томе /data.
FROM python:3.13-slim-trixie@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b
RUN groupadd --system --gid 10001 pyrobot \
    && useradd --system --uid 10001 --gid pyrobot --home-dir /app --no-create-home pyrobot \
    && mkdir -p /data \
    && chown pyrobot:pyrobot /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY app ./app
COPY --from=admin /admin/build ./admin
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYROBOT_DATA_DIR=/data \
    PYROBOT_HTTP_PORT=8080 \
    PYROBOT_ADMIN_DIR=/app/admin
USER pyrobot
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-m", "app.healthcheck", "/healthz"]
CMD ["python", "-m", "app"]
