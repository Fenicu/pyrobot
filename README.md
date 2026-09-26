# pyrobot

Userbot для автоматической игры в StartupWars (@StartupWarsBot). Боты в игре официально разрешены.

Один процесс: kurigram (MTProto) + движок + FastAPI в одном asyncio-цикле, Postgres.
Админка (Svelte) — отдельный проект поверх API этого сервиса.

## Разработка

```bash
uv sync
docker compose -f compose.dev.yml up -d   # Postgres для тестов
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

## Структура

- `app/engine` — движок: типы сообщений, парсеры, реестр команд, конвейер, шлюз действий, транспорт.
- `app/db` — Postgres: модели, миграции, хранилища.
- `app/api` — HTTP API для админки.
