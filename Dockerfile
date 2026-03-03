FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=true \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-cache --no-dev

COPY ./app .

CMD ["uv", "run", "--no-dev", "main.py"]
