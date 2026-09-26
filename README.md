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

## Конфигурация

Все параметры загружаются через переменные окружения с префиксом `PYROBOT_`. Пример: `cp .env.example .env && vi .env`.

| Переменная | Назначение |
|---|---|
| `PYROBOT_DATABASE_URL` | Connection string к PostgreSQL (по умолчанию localhost:55432). |
| `PYROBOT_DATA_DIR` | Путь к директории для хранения игровых данных. |
| `PYROBOT_TG_API_ID` | Telegram API ID (из my.telegram.org, обязательно). |
| `PYROBOT_TG_API_HASH` | Telegram API hash (из my.telegram.org, обязательно). |
| `PYROBOT_ADMIN_LOGIN` | Логин для доступа в админку. |
| `PYROBOT_ADMIN_PASSWORD` | Пароль для админки (если не задан, доступ отключён). |
| `PYROBOT_COOKIE_SECURE` | Использовать флаг Secure для cookies (true в боевой, false в локальной разработке). |
| `PYROBOT_TRANSPORT` | Транспорт Telegram: `kurigram` (боевой MTProto) или `fake` (для тестов). |
| `PYROBOT_LOG_LEVEL` | Уровень логирования (DEBUG, INFO, WARNING, ERROR). |
| `PYROBOT_HTTP_HOST` | IP для привязки HTTP сервера. |
| `PYROBOT_HTTP_PORT` | Порт для HTTP API. |
| `PYROBOT_ACCOUNT_ID` | ID аккаунта в игре (по умолчанию 1). |

## База данных

Postgres для разработки и тестов поднимается через `compose.dev.yml`:

```bash
docker compose -f compose.dev.yml up -d
```

Контейнер слушает `127.0.0.1:55432` и создаёт три базы: `pyrobot` (основная база для разработки),
`pyrobot_test` (юнит- и интеграционные тесты, фикстуры `db`/`clean_db`) и `pyrobot_migtest`
(тест миграций `tests/db/test_migrations.py`). Список тестовых баз задаётся в
`docker/initdb/10-test-dbs.sql`, который накатывается только при первой инициализации volume.

Схема версионируется через Alembic (`app/db/migrations`). Применить миграции:

```bash
uv run alembic upgrade head
```

Новую миграцию генерировать через `uv run alembic revision --autogenerate -m "..."`.

## Структура

- `app/engine` — движок: типы сообщений, парсеры, реестр команд, конвейер, шлюз действий, транспорт.
- `app/db` — Postgres: модели, миграции, хранилища.
- `app/api` — HTTP API для админки.
