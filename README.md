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

Конвейер входящих (`app/engine/pipeline.py`, `app/engine/bus.py`) обрабатывает обновления строго
последовательно: дедуп по ревизии, журнал и снимок состояния пишутся в одной транзакции, публикация
подписчикам — только после фиксации. Сбой журнала (БД) повторяется с backoff без потери и без
переупорядочивания сообщений; ошибка редьюсера не теряет сообщение. Догнанные старые сообщения
(`recovered`) старше `react_max_age` помечаются `reactable=False` — только журнал и снимки, без реакций.
Подписчики шины не должны ждать результатов действий — только быстро перекладывать доставку дальше.

Шлюз действий (`app/engine/gateway`) — единственный путь отправки: любой текст или клик по кнопке
проходит через `ActionGateway`. Команды разбиты на классы (`app/engine/commands.py`): `nav`
(навигация), `action` (игровые действия), `risky` (необратимые), `forbidden`/`donate` (не
отправляются никогда). Правила проверяются при постановке в очередь, при выборе и перед каждой
попыткой отправки: `risky` уходит только при `source=MANUAL` и подтверждённом `risky_confirmed`,
иначе `REJECTED "risky_requires_confirm"`; `action`/`risky` без `Expectation` отклоняются
(`REJECTED "expectation_required"`) — успешный вызов API сам по себе не подтверждает результат в
игре, подтверждает только ответ игры по предикату; только `nav` без ожидания подтверждается сразу
после отправки (`CONFIRMED "sent"`). Kill switch (ручной `kill()` или `settings.engine.killed`)
подавляет всё (`SUPPRESSED "kill_switch"`), `dry_run` подавляет всё, кроме `nav`
(`SUPPRESSED "dry_run"`), блок трат (`block_spending`) отклоняет всё, кроме `nav`
(`REJECTED "blocked:<reason>"`). Истёкший TTL — `REJECTED "expired"`, клик по кнопке вне последней
ревизии сообщения — `REJECTED "stale_button"`, несовпадение `expect_revision` — `REJECTED
"stale_revision"`.

Действие в хранилище (`ActionStore`) проходит `INTENT` (пишется до отправки) → `SENT` → терминальный
статус (`CONFIRMED`/`REFUSED`/`SUPPRESSED`/`OUTCOME_UNKNOWN`/`REJECTED`). Ожидание ответа
регистрируется до отправки вместе с границей журнала (`boundary()` — номер последней записи
журнала на момент отправки): засчитываются только доставки с `journal_id` больше этой границы, с
датой не раньше момента отправки минус 2 секунды, не исходящие и из того же чата. Тайм-аут ожидания
даёт `OUTCOME_UNKNOWN "timeout"` без автоматического повтора. `MemoryActionStore`
(`app/engine/memory.py`) — реализация для тестов; `DbActionStore` (`app/db/actions.py`) — Postgres, с
уникальным `(account_id, idempotency_key)` для дедупликации повторных отправок и
`mark_unfinished_unknown()` для восстановления после рестарта (незавершённые
`INTENT`/`SENT` переводятся в `OUTCOME_UNKNOWN "restart"`).
