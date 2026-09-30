# Мультиаккаунт, этап 1: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Одна установка pyrobot держит несколько аккаунтов владельца — у каждого свой движок,
своя сессия Telegram и свои пути API и админки; нынешняя установка переезжает без перелогина.

**Architecture:** Нынешний `Runtime` (`app/main.py`) делится на процесс (база, вход, HTTP,
`EngineHost`, задачи процесса) и `AccountRuntime` на аккаунт. `EngineHost` держит аренду каждого
аккаунта (advisory-блокировка и `accounts.lease_*`) и выдаёт ему `Fence` — через неё идут все
вызовы Telegram и все пишущие транзакции аккаунта. Сессия kurigram лежит в `tg_sessions`
(`auth_key` зашифрован), своя догонка kurigram выключена, полноту журнала даёт `HistorySync`.
Маршруты API — под `/api/v1/accounts/{account_id}` через зависимость `account_scope`; админка —
под `/a/[account]`.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, Postgres 17,
kurigram 2.2.26, `cryptography` (новая зависимость), SvelteKit 2 / Svelte 5, vitest.

**Spec:** `docs/superpowers/specs/2026-09-30-multiaccount-design.md` — разделы 2–4 и 7–9 в части
этапа 1. Разделы 5–6 — только ограничения на будущее: здесь не реализуются.

## Global Constraints

- **Коммиты.** Автор — `git -c user.name=Fenicu -c user.email=fenafenol@gmail.com commit`.
  Сообщение по-русски, в стиле истории репозитория. **Никаких трейлеров и подписей**:
  `Co-Authored-By`, строки `via […](…)`, упоминания инструментов и моделей запрещены. Один
  коммит — одно логическое изменение; задача может дать несколько коммитов.
- **Проверки перед каждым коммитом** (как в `.forgejo/workflows/ci.yml`):
  `uvx --from 'ruff==0.16.9' ruff check .`, `uvx --from 'ruff==0.16.9' ruff format --check .`,
  `uv run --no-sync mypy`, `uv run --no-sync pytest -q -m "not corpus"` (Postgres —
  `docker compose -f compose.dev.yml up -d`, базы `pyrobot_test` и `pyrobot_migtest`). Изменилось
  API — `uv run python tools/openapi.py`, затем `cd admin && npm run gen:api && npm run check &&
  npm run test`; `openapi.json` и `admin/src/lib/api/schema.d.ts` — в том же коммите.
- pytest идёт с `filterwarnings = error`: незакрытое соединение или задача — падение теста.
- Новая зависимость — только `cryptography` (`uv add cryptography`, `uv.lock` в коммите).
- Этапы 2–3 не делаются: нет ролей, приглашений, воркеров, `NOTIFY` между процессами,
  внутреннего API.
- **Значения из спеки** (дословно):
  - аренда: ключ блокировки `(0x7079726F, account_id)`, TTL 15 с, продление каждые 5 с, запас
    местного срока 3 с, `lock_timeout` продления 2 с, повтор продления через 1 с, повтор чужой
    блокировки через 30 с, ожидание чужой аренды — до её `lease_expires_at` + 1 с;
  - хост: `PYROBOT_MAX_ENGINES` = 20, `PYROBOT_ENGINE_START_GAP_S` = 3, сверка желаемого
    состояния раз в 30 с, 5 сбоев задачи за 10 минут — `error`, пул БД `PYROBOT_DB_POOL_SIZE` = 10
    и `PYROBOT_DB_MAX_OVERFLOW` = 20, чистка пачками по 5000, временный клиент для `log_out` —
    таймаут 30 с;
  - Telegram: `PIPELINE_QUEUE_MAX` = 10000, `OVERLOAD_HIGH` = 5000, `OVERLOAD_LOW` = 500; сверка
    истории — до 1000 сообщений за проход, хвост 50, периодический проход раз в 5 минут, повтор
    неудачного через 30 с, 1, 2, 5 минут; `recovered` — приход позже даты больше чем на 60 с;
    коды входа — `PYROBOT_TG_CODES_PER_HOUR` = 10 на хост и 3 в час на аккаунт;
  - API: ожидание регистрации движка 5 с, затем 503 `engine_starting` с `Retry-After: 2`;
  - шифрование: AES-256-GCM, ключ 32 байта urlsafe base64, шифротекст = байт версии + 12 байт
    nonce + шифротекст с тегом, AAD `pyrobot:<назначение>:<account_id>`, контрольная запись —
    AAD `pyrobot:key_check:0`.
- **Коды ошибок и уведомлений** (дословно): 404 `account not found`; 503 `engine not running`,
  503 `engine_starting`; 409 `name_taken`, `capacity_reached`, `version_conflict`; 422
  `confirm_name_mismatch`, `chat_is_self`; 429 `tg_code_rate_limited`; статусы входа
  `unexpected_user`, `tg_user_taken`, `chat_is_self`; `status_reason` `crash_loop:<задача>`;
  `host_reason` `locked_elsewhere`; уведомления аккаунта `account_crash_loop` (error),
  `tg_session_import_failed` (warn), `tg_session_unreadable` (error), `history_gap_truncated`
  (warn), `account_overload` (warn).
- Комментарии, логи и тексты — по-русски, как в окружающем коде.

## Review Focus

1. **Обновление живой установки.** Файл `pyrobot.session` с рабочей сессией и
   `settings.telegram.expected_user_id` в базе: после миграции и старта аккаунт 1 онлайн без
   входа, привязка на месте, файл переименован в `pyrobot.session.migrated`. Тест — в задаче 10.
2. **Ключ при первом старте после обновления.** `PYROBOT_SECRET_KEY` не задан или не тот —
   процесс не стартует, в логе строка с причиной (и командой генерации ключа), ни одна сессия не
   удалена; удаление — только с `PYROBOT_SECRET_KEY_RESET=1`. Тесты — в задачах 2 и 10.
3. **Два аккаунта в двух вкладках.** SSE, непрочитанные и кэш страниц аккаунта A не показывают
   данных B; ответ запроса, отправленного до переключения, не попадает в контекст нового
   аккаунта. Тесты — в задачах 7 и 14.
4. **Аккаунт падает при старте из-за своих настроек.** Остальные аккаунты работают;
   пользователь правит настройки прямой записью и включает аккаунт — без перезапуска процесса.
   Тест — в задаче 8.
5. **Медленный Telegram или FloodWait в проходе сверки на старте.** Движок и шлюз работают,
   старт не ждёт прохода, отметка не сдвигается, проход повторяется по расписанию. Тест — в
   задаче 11.

## Карта файлов

Новые:

| Файл | Ответственность |
|---|---|
| `app/db/migrations/versions/0011_accounts_registry.py` | колонки `accounts`, перенос привязки, `setval`, индексы |
| `app/db/migrations/versions/0012_tg_sessions.py` | `tg_sessions`, `tg_peers`, `tg_chat_marks`, `server_meta` |
| `app/db/migrations/versions/0013_chat_marks_seed.py` | отметки аккаунта 1 по журналу |
| `app/db/accounts.py` | `AccountRepo`: реестр, привязка, чистка удаляемого |
| `app/db/crypto.py` | `SecretBox`, разбор ключа, `key_check`, сброс |
| `app/db/tg_storage.py` | `PgSessionStorage`, импорт `pyrobot.session` |
| `app/db/chat_marks.py` | `ChatMarks`: отметки сверки истории |
| `app/engine/fence.py` | `Fence`, `LeaseLost`: ограда вызовов Telegram и записей |
| `app/engine/host/lease.py` | `LeaseManager`: блокировки, захват, продление, освобождение |
| `app/engine/host/account.py` | `AccountRuntime`: движок одного аккаунта |
| `app/engine/host/host.py` | `EngineHost`: все аккаунты процесса, желаемое состояние, чистка |
| `app/engine/host/codes.py` | `CodeLimiter`: лимит кодов входа на хост и аккаунт |
| `app/engine/transport/history.py` | `HistorySync`: проходы сверки истории |
| `app/logctx.py` | ContextVar аккаунта и фильтр логов |
| `app/api/scope.py` | `AccountScope`, зависимость `account_scope` |
| `app/api/routes_accounts.py` | `/accounts`, `/accounts/{id}/engine/restart`, `/host/status` |
| `app/tools/__init__.py`, `app/tools/users.py` | CLI `set-password` |
| `tools/bench_engines.py` | замер N движков (не в CI) |

Меняются: `app/main.py`, `app/config.py`, `app/db/base.py`, `app/db/models.py`,
`app/db/journal.py`, `app/db/settings_store.py`, `app/db/actions.py`, `app/db/planner.py`,
`app/db/metro.py`, `app/db/notifications.py`, `app/db/retention.py`, `app/engine/pipeline.py`,
`app/engine/supervisor.py`, `app/engine/facade.py`, `app/engine/tg_auth.py`,
`app/engine/settings.py`, `app/engine/transport/kurigram.py`, `app/api/*.py`, `tools/login.py`,
`admin/src/**`, `deploy/render-env.sh`, `.env.example`, `README.md`. Удаляются `app/db/lock.py`
и `tests/db/test_lock.py`.

---

## Часть A. База

### Task 1: Реестр аккаунтов и привязка к Telegram в `accounts`

**Files:**
- Create: `app/db/migrations/versions/0011_accounts_registry.py`, `app/db/accounts.py`,
  `tests/db/test_accounts.py`
- Modify: `app/db/models.py` (`Account`), `app/engine/settings.py` (убрать `TelegramSection`,
  `"telegram."` из `_RESTART_REQUIRED`), `app/main.py:196-201,298-304`,
  `app/engine/facade.py` / `app/api/routes_engine.py` (`TgStatusOut.bound_user_id`),
  `tools/login.py:128-139`, `tests/conftest.py`, `tests/db/test_migrations.py`,
  `tests/api/test_login_tool.py`, `openapi.json`, `admin/src/lib/api/schema.d.ts`

**Interfaces:**
- Produces (`app/db/accounts.py`):
  - `AccountStatus = Literal["enabled", "disabled", "error", "deleting"]`
  - `@dataclass(frozen=True) class AccountInfo`: `id: int`, `owner_id: int | None`, `name: str`,
    `status: AccountStatus`, `status_reason: str | None`, `tg_user_id: int | None`,
    `engine_generation: int`
  - исключения `NameTaken`, `CapacityReached`, `TgUserTaken`
  - `class AccountRepo(db: Database)`:
    `get(account_id) -> AccountInfo | None`; `owned(owner_id) -> list[AccountInfo]` (по `id`);
    `with_status(*statuses: AccountStatus) -> list[AccountInfo]`;
    `create(owner_id, name, *, capacity: int) -> AccountInfo` (строка `accounts` `enabled` и
    строка `settings` с `Settings()` версии 1 — одной транзакцией; `NameTaken`,
    `CapacityReached`, если `enabled` стало бы больше `capacity`);
    `update(account_id, *, name: str | None = None, enabled: bool | None = None,
    capacity: int) -> AccountInfo` (`enabled=True` ставит `enabled` и очищает
    `status_reason`, `False` — `disabled`);
    `set_status(account_id, status, reason: str | None) -> None`;
    `restart(account_id) -> None` (`engine_generation + 1`);
    `mark_deleting(account_id) -> None`;
    `bind_telegram(account_id, tg_user_id) -> None` (только если `tg_user_id IS NULL`;
    занят другим аккаунтом — `TgUserTaken`);
    `adopt_orphans() -> int` (аккаунты с `owner_id` NULL получают первую по `id` учётку).
  - `updated_at` обновляется каждой записью.
- `TgStatusOut` получает `bound_user_id: int | None` — `accounts.tg_user_id`.

- [ ] **Step 1: Тесты миграции**

В `tests/db/test_migrations.py`:

```python
async def test_0011_moves_binding_and_adopts_owner() -> None:
    # до 0011: admin_users(id=7), accounts(id=1), settings(account_id=1) с
    # data = {"telegram": {"expected_user_id": 267519921}, "engine": {"mode": "live"}}
    # upgrade 0011:
    assert account["owner_id"] == 7 and account["name"] == "Основной"
    assert account["status"] == "enabled" and account["tg_user_id"] == 267519921
    assert "telegram" not in settings_data and settings_data["engine"]["mode"] == "live"
    # nextval('accounts_id_seq') после upgrade > max(id)
    # downgrade 0010: settings_data["telegram"] == {"expected_user_id": 267519921}

async def test_0011_without_admin_leaves_owner_null() -> None: ...
```

- [ ] **Step 2: Тесты репозитория** в `tests/db/test_accounts.py` (`pytestmark = pytest.mark.db`):

```python
async def test_create_adds_enabled_account_with_default_settings(clean_db) -> None:
    acc = await repo.create(admin_id, "Второй", capacity=20)
    assert acc.status == "enabled" and acc.engine_generation == 0
    assert (await settings_row(acc.id)).version == 1

async def test_create_name_taken_per_owner(clean_db) -> None: ...          # NameTaken
async def test_create_capacity_counts_only_enabled(clean_db) -> None:
    # capacity=2: 1 enabled + 1 disabled + 1 error + 1 deleting → create проходит;
    # ещё один enabled → CapacityReached
async def test_update_enable_over_capacity_rejected(clean_db) -> None: ...
async def test_second_account_after_migration_gets_next_id(clean_db) -> None: ...
async def test_bind_telegram_once_and_unique(clean_db) -> None:
    await repo.bind_telegram(1, 111)
    await repo.bind_telegram(1, 222)            # уже привязан — без изменений
    assert (await repo.get(1)).tg_user_id == 111
    with pytest.raises(TgUserTaken):
        await repo.bind_telegram(second.id, 111)
async def test_adopt_orphans_gives_first_admin(clean_db) -> None: ...
async def test_restart_bumps_generation(clean_db) -> None: ...
```

- [ ] **Step 3: Запустить — падают**

Run: `uv run --no-sync pytest tests/db/test_accounts.py tests/db/test_migrations.py -q`
Expected: FAIL (`ModuleNotFoundError: app.db.accounts`, нет колонок).

- [ ] **Step 4: Модель и миграция**

`Account` в `app/db/models.py` — колонки из раздела 4.1 спеки: `owner_id` (FK
`admin_users.id` ON DELETE RESTRICT, NULL), `name` varchar(64) NOT NULL, UNIQUE
(`owner_id`, `name`), `status` varchar(16) NOT NULL CHECK, `status_reason` text,
`engine_generation` bigint NOT NULL default 0, `lease_holder` text, `lease_epoch` bigint NOT
NULL default 0, `lease_expires_at` timestamptz, `updated_at` timestamptz NOT NULL; частичный
UNIQUE индекс `tg_user_id` WHERE NOT NULL; индексы `notifications (account_id, id)` и
`settings_history (account_id, id)`.

`0011` (down `0010`): колонки; `owner_id` — `(SELECT min(id) FROM admin_users)`; `name` —
`'Основной'` у наименьшего `id`, иначе `'Аккаунт ' || id`; `tg_user_id` ←
`(data->'telegram'->>'expected_user_id')::bigint`; `data = data - 'telegram'`;
`SELECT setval('accounts_id_seq', (SELECT max(id) FROM accounts))`. Downgrade возвращает
`telegram.expected_user_id` в JSON из `tg_user_id` и удаляет колонки и индексы.

`tests/conftest.py`: фикстура `db` вставляет `accounts(id=1, name='Основной')`; `clean_db`
после `TRUNCATE … CASCADE` (он чистит и `accounts` через FK `owner_id`) вставляет её снова и
делает `setval`.

- [ ] **Step 5: `AccountRepo`** по Interfaces.

- [ ] **Step 6: Привязка из `accounts`**

`TelegramSection` и поле `Settings.telegram` удаляются (старые JSON с ключом `telegram`
читаются: лишние поля игнорируются). `Runtime._start` берёт `expected_user_id` из
`AccountRepo.get(account_id).tg_user_id`, `_bind_telegram` вызывает `bind_telegram`.
`TgStatusOut.bound_user_id`; `tools/login.py` сверяет `user_id` с `tg/status.bound_user_id`
вместо `settings.values.telegram`. `uv run python tools/openapi.py`, `npm run gen:api`.

- [ ] **Step 7: Запустить — проходят**

Run: `uv run --no-sync pytest -q -m "not corpus"` и проверки из Global Constraints.
Expected: PASS.

- [ ] **Step 8: Commit** — «Реестр аккаунтов: владелец, имя, статус, поколение движка и аренда в
  `accounts`; привязка к пользователю Telegram переезжает из настроек в `accounts.tg_user_id`».

### Task 2: Ключ шифрования и таблицы сессий Telegram

**Files:**
- Create: `app/db/crypto.py`, `app/db/migrations/versions/0012_tg_sessions.py`,
  `tests/db/test_crypto.py`
- Modify: `app/db/models.py`, `app/config.py`, `pyproject.toml`, `uv.lock`, `.env.example`,
  `deploy/render-env.sh`, `tests/test_render_env.py`, `tests/test_config.py`

**Interfaces:**
- Produces (`app/db/crypto.py`):
  - `class SecretKeyError(Exception)` (текст — для лога), `class Undecryptable(Exception)`
  - `parse_key(value: str | None) -> bytes` — urlsafe base64 ровно 32 байта, иначе
    `SecretKeyError` с текстом `PYROBOT_SECRET_KEY: нужен ключ 32 байта в urlsafe base64 —
    python -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`
  - `class SecretBox(key: bytes)`: `seal(data: bytes, purpose: str, account_id: int) -> bytes`,
    `open(blob: bytes, purpose: str, account_id: int) -> bytes` (`Undecryptable`)
  - `async def ensure_key(db: Database, box: SecretBox, *, reset: bool) -> None` — пишет
    `server_meta.key_check`, если её нет; не расшифровывается и `reset` ложен —
    `SecretKeyError("ключ не подходит к базе")`; `reset` истинен — удаляет все `tg_sessions` и
    `tg_peers`, пишет `key_check` новым ключом, пишет в лог warning
- Модели: `TgSession`, `TgPeer`, `TgChatMark`, `ServerMeta` — колонки из раздела 4.1 спеки;
  `TgChatMark`: PK (`account_id`, `chat_id`, `from_id`), `msg_id` bigint NOT NULL,
  `updated_at`. FK `account_id` → `accounts.id` без каскада.
- `AppConfig`: `secret_key: SecretStr | None = None`, `secret_key_reset: bool = False`,
  `db_pool_size: int = 10`, `db_max_overflow: int = 20`, `max_engines: int = 20`,
  `engine_start_gap_s: float = 3.0`, `tg_codes_per_hour: int = 10`.

- [ ] **Step 1: Тесты** (`tests/db/test_crypto.py`):

```python
def test_seal_open_roundtrip_and_format() -> None:
    blob = box.seal(b"key", "auth_key", 1)
    assert blob[0] == 1 and len(blob) == 1 + 12 + 3 + 16
    assert box.open(blob, "auth_key", 1) == b"key"

def test_open_rejects_other_account_purpose_or_key() -> None: ...   # Undecryptable
@pytest.mark.parametrize("value", [None, "", "short", base64_of_31_bytes])
def test_parse_key_rejects_bad_values(value) -> None: ...           # SecretKeyError, есть «secrets.token_bytes(32)»

async def test_ensure_key_writes_check_once(clean_db) -> None: ...
async def test_ensure_key_wrong_key_refuses_and_keeps_sessions(clean_db) -> None:
    # tg_sessions есть; другой ключ, reset=False → SecretKeyError, строка tg_sessions на месте
async def test_ensure_key_reset_drops_sessions_and_rewrites_check(clean_db) -> None: ...
async def test_ensure_key_reset_with_right_key_does_nothing(clean_db) -> None: ...
```

Плюс в `tests/db/test_migrations.py` — `tg_sessions`, `tg_peers`, `tg_chat_marks`,
`server_meta` в наборе таблиц после upgrade.

- [ ] **Step 2: Запустить — падают.** `uv run --no-sync pytest tests/db/test_crypto.py -q`

- [ ] **Step 3: Реализация.** `uv add cryptography`; `AESGCM` с nonce `os.urandom(12)`; AAD
  `f"pyrobot:{purpose}:{account_id}".encode()`; миграция `0012` (down `0011`) создаёт четыре
  таблицы; `.env.example` и `deploy/render-env.sh` получают `PYROBOT_SECRET_KEY` (render-env
  генерирует ключ, если его нет в прежнем env, и не меняет существующий).

- [ ] **Step 4: Запустить — проходят**, плюс проверки из Global Constraints.

- [ ] **Step 5: Commit** — «Ключ шифрования сервера: AES-256-GCM с привязкой блоба к аккаунту,
  контрольная запись `key_check` и осознанный сброс сессий; таблицы сессий Telegram».

### Task 3: Сессия kurigram в Postgres

**Files:**
- Create: `app/db/tg_storage.py`, `tests/db/test_tg_storage.py`

**Interfaces:**
- Consumes: `SecretBox`, `Undecryptable` (задача 2).
- Produces (`app/db/tg_storage.py`):
  - `class PgSessionStorage(pyrogram.storage.Storage)`:
    `__init__(db: Database, account_id: int, box: SecretBox, peer_ids: Callable[[], set[int]])`.
    Поля сессии — строка `tg_sessions` (`auth_key` через `box.seal(..., "auth_key", id)`),
    запись в `save()` и при изменении `auth_key`/`user_id`/`dc_id`; состояние обновлений и
    usernames — только в памяти; `update_peers` держит всё в памяти, а пиры с `id` из
    `peer_ids()` ещё и пишет в `tg_peers`; `open()` загружает строку сессии и `tg_peers`,
    повреждённый блоб — `Undecryptable`; `close()` + повторный `open()` того же объекта
    работают; `delete()` удаляет строки аккаунта в `tg_sessions` и `tg_peers`.
  - `async def import_session_file(path: Path, storage: PgSessionStorage, peer_ids: set[int])
    -> bool` — читает файл через `SQLiteStorage` kurigram (поля сессии и пиры из `peer_ids`),
    пишет в `storage`, переименовывает файл в `<имя>.migrated`; нет файла — `False`.

- [ ] **Step 1: Тесты** (`tests/db/test_tg_storage.py`, `pytestmark = pytest.mark.db`).
  Общий набор проверок интерфейса `Storage` параметризован двумя фабриками —
  `SQLiteStorage(in_memory=True)` и `PgSessionStorage`:

```python
@pytest.fixture(params=["sqlite", "pg"])
async def storage(request, clean_db) -> AsyncIterator[Storage]: ...

async def test_session_fields_roundtrip(storage) -> None:
    await storage.dc_id(2); await storage.auth_key(b"k" * 256); await storage.user_id(267519921)
    assert await storage.dc_id() == 2 and await storage.auth_key() == b"k" * 256

async def test_peers_in_memory(storage) -> None:
    await storage.update_peers([(-1001109615116, 42, "supergroup", None)])
    assert (await storage.get_peer_by_id(-1001109615116)).access_hash == 42

async def test_update_state_in_memory(storage) -> None: ...
```

Только для `PgSessionStorage`:

```python
async def test_session_survives_new_object(clean_db) -> None: ...        # новый объект, open() — те же поля
async def test_only_configured_peers_persist(clean_db) -> None:
    # peer_ids = {game, swinfo_user}; update_peers(game, swinfo_user, other); новый объект:
    # game и swinfo_user находятся, other — нет (KeyError/None, как у Storage)
async def test_update_state_not_persisted(clean_db) -> None: ...
async def test_auth_key_encrypted_at_rest(clean_db) -> None:
    assert b"k" * 32 not in raw_row.auth_key
async def test_foreign_blob_is_undecryptable(clean_db) -> None:
    # блоб аккаунта 1 переписан в строку аккаунта 2 → open() аккаунта 2 → Undecryptable
async def test_close_then_open_same_object(clean_db) -> None: ...
async def test_delete_removes_rows(clean_db) -> None: ...
async def test_import_session_file(tmp_path, clean_db) -> None:
    # pyrobot.session собран SQLiteStorage (auth_key, dc_id, user_id, пиры game/other)
    assert await import_session_file(path, storage, {game}) is True
    assert (tmp_path / "pyrobot.session.migrated").exists() and not path.exists()
    # в базе auth_key и пир game; other не перенесён
async def test_import_missing_file_is_false(tmp_path, clean_db) -> None: ...
```

- [ ] **Step 2: Запустить — падают.** `uv run --no-sync pytest tests/db/test_tg_storage.py -q`

- [ ] **Step 3: Реализация** по Interfaces. Методы `Storage` — `async`, как в
  `pyrogram/storage/storage.py`; `get_peer_by_id` строит `InputPeer*` тем же способом, что
  `SQLiteStorage` (`utils.get_input_peer`).

- [ ] **Step 4: Запустить — проходят.**

- [ ] **Step 5: Commit** — «Сессия kurigram в Postgres: поля сессии и пиры настроенных чатов в
  базе, `auth_key` зашифрован; состояние обновлений в памяти; импорт `pyrobot.session`».

## Часть B. Движок аккаунта

### Task 4: Ограда аренды — `Fence` и ограждённые записи

**Files:**
- Create: `app/engine/fence.py`, `tests/engine/test_fence.py`, `tests/db/test_fenced_writes.py`
- Modify: `app/db/journal.py`, `app/db/settings_store.py`, `app/db/actions.py`,
  `app/db/planner.py`, `app/db/metro.py`, `app/db/notifications.py`, `app/engine/pipeline.py`,
  `tests/engine/test_pipeline.py`

**Interfaces:**
- Produces (`app/engine/fence.py`):
  - `class LeaseLost(Exception)`
  - `class Fence`: `__init__(account_id: int, epoch: int, deadline: float, *, monotonic:
    Callable[[], float] = time.monotonic)`; атрибуты `account_id`, `epoch`, `deadline`;
    `on_lost: Callable[[], None] | None` — вызывается один раз при первом обнаружении потери;
    `alive -> bool` (не отозвана и `monotonic() < deadline`); `extend(epoch, deadline) -> None`
    (другая эпоха или отозвана — ничего); `revoke() -> None`; `check() -> None` (`LeaseLost`);
    `async call(fn: Callable[[], Awaitable[T]]) -> T` — `check()`, затем `fn()` под
    `asyncio.timeout` до `deadline` (в часах цикла); сработал именно срок — `LeaseLost`, свой
    `TimeoutError` вызова пробрасывается как есть; `async guard(session: AsyncSession) -> None`
    — `check()`, затем `SELECT 1 FROM accounts WHERE id = :id AND lease_epoch = :epoch FOR
    SHARE`; строки нет — `revoke()` и `LeaseLost`.
- Хранилища `DbJournal`, `DbSettingsStore`, `DbActionStore`, `DbPlannerStore`,
  `DbMetroRunStore`, `DbNotifier` получают keyword-параметр `fence: Fence | None = None`; каждая
  пишущая транзакция первым делом вызывает `await fence.guard(session)`, если ограда задана.
  Чтения не ограждаются. `DbSettingsStore.update` при `LeaseLost` не превращает его в
  `SettingsConflict`.
- `Pipeline._append`: `LeaseLost` не повторяется — пробрасывается сразу.

- [ ] **Step 1: Тесты ограды** (`tests/engine/test_fence.py`, часы подменены):

```python
def test_alive_until_deadline_and_extend_same_epoch_only() -> None: ...
def test_on_lost_called_once() -> None: ...                  # revoke(); revoke(); check() → один вызов
async def test_call_refused_after_deadline() -> None: ...     # LeaseLost, fn не вызвана
async def test_call_cut_at_deadline() -> None:
    # fn ждёт дольше срока (как invoke kurigram, ждущий запуска сессии 15 с) → LeaseLost
async def test_call_own_timeout_passes_through() -> None: ...  # fn бросает TimeoutError до срока
```

- [ ] **Step 2: Тесты ограждённых записей** (`tests/db/test_fenced_writes.py`, db):

```python
async def test_guard_passes_with_current_epoch(clean_db) -> None: ...
@pytest.mark.parametrize("store", ["journal", "settings", "actions", "planner", "metro", "notifier"])
async def test_write_with_stale_epoch_raises_lease_lost(clean_db, store) -> None:
    # accounts.lease_epoch = 8, Fence(epoch=7) → LeaseLost, строки не добавлены
async def test_settings_lease_lost_is_not_version_conflict(clean_db) -> None: ...
async def test_acquire_waits_for_fenced_write(clean_db) -> None:
    # транзакция A: guard (FOR SHARE) и пауза; B: UPDATE accounts SET lease_epoch = lease_epoch + 1
    # на другом соединении — завершается только после коммита A; новая запись с прежней эпохой → LeaseLost
```

и в `tests/engine/test_pipeline.py`:

```python
async def test_append_lease_lost_not_retried() -> None:
    # журнал бросает LeaseLost: process() пробрасывает его после одной попытки, состояние не меняется
```

- [ ] **Step 3: Запустить — падают.**
  `uv run --no-sync pytest tests/engine/test_fence.py tests/db/test_fenced_writes.py tests/engine/test_pipeline.py -q`

- [ ] **Step 4: Реализация** по Interfaces.

- [ ] **Step 5: Запустить — проходят**, плюс проверки из Global Constraints.

- [ ] **Step 6: Commit** — «Ограда аренды: вызовы и записи аккаунта не выходят за местный срок
  аренды, пишущие транзакции проверяют эпоху с блокировкой строки аккаунта; конвейер не повторяет
  запись при потере аренды».

### Task 5: Аренда аккаунта — `LeaseManager`

**Files:**
- Create: `app/engine/host/__init__.py`, `app/engine/host/lease.py`,
  `tests/engine/host/__init__.py`, `tests/engine/host/test_lease.py`

**Interfaces:**
- Consumes: `Fence` (задача 4).
- Produces (`app/engine/host/lease.py`):
  - `@dataclass(frozen=True) class Busy`: `reason: Literal["locked_elsewhere", "lease_active"]`,
    `retry_in_s: float`
  - `class LeaseManager`: `__init__(db: Database, holder: str, *, ttl_s: float = 15.0,
    renew_every_s: float = 5.0, margin_s: float = 3.0, lock_timeout_s: float = 2.0, retry_s:
    float = 1.0, busy_retry_s: float = 30.0, monotonic: Callable[[], float] = time.monotonic)`;
    `holder: str`; `async open() -> None` (выделенное соединение `AUTOCOMMIT`, `SET
    lock_timeout`); `async close() -> None`; `async acquire(account_id) -> Fence | Busy`;
    `async release(fence) -> None`; `async renew_once() -> None`; `async run() -> None` (цикл
    продления и переподключения); `healthy() -> bool`; `held() -> list[int]`.
- Протокол — пп. 1–7 раздела 4.2 спеки:
  - `acquire`: блокировка берётся, только если её нет в памяти хоста; не взята —
    `Busy("locked_elsewhere", busy_retry_s)`; условный `UPDATE … RETURNING lease_epoch`; ноль
    строк — `Busy("lease_active", <до lease_expires_at> + 1)` (блокировка остаётся за хостом);
    успех — `Fence(account_id, epoch, t + ttl_s − margin_s)`, где `t` снят до запроса.
  - `renew_once`: пары `(id, epoch)` живых оград, `t` до запроса, один `UPDATE … RETURNING id,
    lease_epoch`; ответ разбирается по парам: ограда аккаунта уже в другой эпохе или удалена —
    пара игнорируется; пара совпала и вернулась — `extend`; совпала и не вернулась — `revoke`.
    Ошибка `lock_timeout` — повтор через `retry_s`.
  - Обрыв соединения: блокировки из памяти забываются, прежние эпохи больше не продлеваются
    (ограды истекают сами), соединение открывается заново.
  - `release`: ограда убирается из набора продления; если `fence.alive` — `UPDATE accounts SET
    lease_holder = NULL, lease_expires_at = NULL WHERE id AND lease_holder AND lease_epoch`;
    затем `pg_advisory_unlock(0x7079726F, id)`.

- [ ] **Step 1: Тесты** (`tests/engine/host/test_lease.py`, db; два менеджера с разными
  `holder` на одной базе; `ttl_s=1.0`, `margin_s=0.3`, `lock_timeout_s=0.1`, подменённые часы
  там, где нужен местный срок):

```python
async def test_acquire_returns_fence_with_epoch_and_local_deadline(clean_db) -> None: ...
async def test_other_host_locked_elsewhere(clean_db) -> None: ...
async def test_lock_connection_killed_new_holder_waits_for_expiry(clean_db) -> None:
    # A держит; pg_terminate_backend(соединение блокировок A); B.acquire → Busy("lease_active");
    # после lease_expires_at B получает ограду; ограда A к этому моменту уже не alive (A.deadline < B старт)
async def test_same_host_reacquires_own_lease_immediately(clean_db) -> None: ...
async def test_lock_not_taken_twice_on_same_connection(clean_db) -> None:
    # два acquire одного аккаунта одним хостом → в pg_locks одна запись этого ключа за pid
async def test_renew_extends_and_stale_epoch_revoked(clean_db) -> None: ...
async def test_late_renew_response_for_old_epoch_ignored(clean_db) -> None:
    # продление собрано для эпохи 7; аккаунт освобождён и захвачен снова (эпоха 8); ответ по эпохе 7
    # разбирается после → ограда 8 alive, deadline не изменился
async def test_release_clears_holder_and_prepared_renew_does_not_revive(clean_db) -> None:
    # после release: lease_holder и lease_expires_at NULL; продление, собранное до release,
    # выполнено после → 0 строк; B.acquire сразу успешен
async def test_release_after_deadline_writes_nothing(clean_db) -> None: ...
async def test_renew_lock_timeout_retries(clean_db) -> None:
    # открытая ограждённая транзакция держит FOR SHARE дольше lock_timeout; продление повторяется
    # через retry_s и проходит после её коммита
async def test_reconnect_does_not_renew_old_epochs(clean_db) -> None: ...
```

- [ ] **Step 2: Запустить — падают.** `uv run --no-sync pytest tests/engine/host -q`

- [ ] **Step 3: Реализация** по Interfaces; все запросы — по соединению блокировок, по одному
  на транзакцию, ожидания — вне транзакций.

- [ ] **Step 4: Запустить — проходят.**

- [ ] **Step 5: Commit** — «Аренда аккаунта: advisory-блокировка на аккаунт и аренда с TTL в
  `accounts`, продление одним запросом с разбором по эпохам, освобождение с очисткой держателя».

### Task 6: `AccountRuntime` — движок одного аккаунта

**Files:**
- Create: `app/engine/host/account.py`, `app/logctx.py`,
  `tests/engine/host/test_account_runtime.py`
- Modify: `app/main.py` (выносится содержимое `_start`/`stop`), `app/engine/supervisor.py`,
  `app/engine/facade.py` (`lock_ok` ← `fence.alive`), `app/__main__.py` (формат логов),
  `tests/engine/test_supervisor.py`, `tests/test_logging.py`, `tests/test_runtime.py`
- Delete: `app/db/lock.py`, `tests/db/test_lock.py`

**Interfaces:**
- Consumes: `Fence`, `LeaseManager`, `Busy` (задачи 4–5); `AccountRepo`, `AccountInfo`
  (задача 1).
- Produces:
  - `app/logctx.py`: `current_account: ContextVar[int | None]`; `class AccountLogFilter(
    logging.Filter)` — ставит `record.account` (`"-"` вне аккаунта); формат логов в
    `app/__main__.py` получает `account=%(account)s`.
  - `Supervisor(notifier, *, base_s=1.0, max_s=60.0, crash_limit: int = 5, crash_window_s:
    float = 600.0, on_crash_loop: Callable[[str], Awaitable[None]] | None = None, monotonic=
    time.monotonic)`: сбой одной задачи `crash_limit` раз за `crash_window_s` — один вызов
    `on_crash_loop(name)`, задача больше не перезапускается.
  - `app/engine/host/account.py`:
    - `@dataclass(frozen=True) class RuntimeDeps`: `db: Database`, `config: AppConfig`,
      `accounts: AccountRepo`, `lag: LoopLagMonitor`
    - `class AccountRuntime`: `__init__(account: AccountInfo, deps: RuntimeDeps, fence: Fence, *,
      on_crash_loop: Callable[[int, str], Awaitable[None]])`; атрибуты `account_id`,
      `generation` (`account.engine_generation`), `fence`, `stream: EventStream`, `settings:
      DbSettingsStore`, `facade: EngineFacade | None`, `tg: TgAuthManager | None`;
      `async start() -> None` — прежний `Runtime._start` без блокировки и без задач процесса,
      хранилища с `fence`, `current_account` выставлен до создания задач; `async stop() -> None`
      — прежний порядок `Runtime.stop` (планировщик, шлюз, транспорт, доработка конвейера,
      супервизор); `async abort() -> None` — без доработки конвейера и без финальных записей
      (транспорт — `abort()` из задачи 10, до неё — `stop()` транспорта), затем отмена задач.
- `app/main.py` после задачи: процесс держит `LeaseManager` (holder — `uuid4().hex`) и один
  `AccountRuntime` аккаунта `config.account_id`; `Busy` — уведомление `second_instance`, как
  сейчас, и процесс без движка; `fence.on_lost` — `abort()` и уведомление `lock_lost`. Задачи
  процесса (`session-purge`, `retention`, `lag`, продление аренды) — в супервизоре процесса.

- [ ] **Step 1: Тесты**

```python
# tests/engine/test_supervisor.py
async def test_crash_loop_after_five_failures_in_window() -> None:
    # задача падает 5 раз за 600 с (подменённые часы) → on_crash_loop("gateway") один раз, рестартов нет
async def test_failures_outside_window_do_not_count() -> None: ...

# tests/test_logging.py
async def test_account_filter_marks_records_from_account_tasks() -> None:
    # запись из задачи, созданной при current_account = 7 → record.account == 7; вне — "-"

# tests/engine/host/test_account_runtime.py (db, transport="fake")
async def test_two_runtimes_share_process_without_crosstalk(clean_db) -> None:
    # аккаунты 1 и 2, у каждого свой EventStream и свои задачи супервизора; stop() одного не
    # трогает другого (его facade.status() работает)
async def test_stop_drains_pipeline_abort_does_not(clean_db) -> None: ...
async def test_writes_after_lost_lease_refused(clean_db) -> None:
    # fence.revoke() → запись настроек через facade → LeaseLost, строка settings не изменилась
```

`tests/test_runtime.py` — прежние сценарии проходят; «второй экземпляр» теперь — второй
`LeaseManager` на той же базе.

- [ ] **Step 2: Запустить — падают.**

- [ ] **Step 3: Реализация** по Interfaces. `SingleInstanceLock` и `_watch_lock` удаляются.

- [ ] **Step 4: Запустить — проходят**, плюс проверки из Global Constraints.

- [ ] **Step 5: Commit** — «Движок аккаунта отделён от процесса: `AccountRuntime` со своим
  супервизором и оградой аренды, счётчик сбоев задач, аккаунт в каждой строке лога; единственная
  блокировка процесса заменена арендой аккаунта».

## Часть C. API под аккаунтом

### Task 7: Пути аккаунта, `account_scope` и аккаунт без движка

**Files:**
- Create: `app/api/scope.py`, `tests/api/test_access_matrix.py`,
  `tests/api/test_engine_down.py`, `admin/src/lib/api/account.ts`,
  `admin/src/lib/api/account.test.ts`
- Modify: `app/api/routes_engine.py`, `routes_state.py`, `routes_settings.py`,
  `routes_planner.py`, `routes_journal.py`, `routes_commands.py` (каталог `GET /scenarios`
  остаётся на глобальном роутере), `routes_reference.py`, `routes_daily.py`, `routes_events.py`,
  `app/api/container.py`, `app/api/confirm.py`, `app/api/errors.py`, `app/api/openapi.py`,
  `app/db/settings_store.py`, `app/engine/tg_auth.py` (`TgState.STOPPED`), `app/main.py`,
  `tools/login.py`, `tests/api/*.py`, `tests/test_openapi.py`, `tests/test_runtime.py`,
  `openapi.json`; админка — `src/lib/api/client.ts`, `src/lib/api/errors.ts`,
  `src/lib/app.svelte.ts` и все места вызова из карты ниже, их тесты
- Места вызова в админке: `stores/engine.svelte.ts:25`, `controls.ts:6,10,14,20,22`,
  `components/telegram/TelegramView.svelte:40,82,87,93,104`, `stores/character.svelte.ts:41`,
  `settings/editor.svelte.ts:75,139`, `components/settings/SettingsHistory.svelte:45`,
  `plan/store.svelte.ts:77`, `stores/journal.svelte.ts:302,325`,
  `components/journal/DecisionDetail.svelte:34`, `ActionDetail.svelte:25`,
  `components/control/RecentRuns.svelte:26`, `components/journal/RunSteps.svelte:30`,
  `components/control/ManualCommand.svelte:36`, `components/journal/MessageDetail.svelte:47`,
  `components/control/ScenarioRunner.svelte:30`, `metrics/series.ts:72`,
  `components/metro/MetroView.svelte:31,51`,
  `components/notifications/UnrecognizedList.svelte:29,59`, `stores/unread.svelte.ts:17`,
  `components/notifications/NotificationList.svelte:33,76`, `daily/store.svelte.ts:71`,
  `client.ts:8` (`EVENTS_URL`); тесты с путями `/api/v1/…` — `api/client.test.ts`,
  `components/home/home.test.ts`, `control/control.test.ts`, `journal/journal.test.ts`,
  `stores/journal.test.ts`, `settings/settings.test.ts`, `telegram/telegram.test.ts`,
  `metro/metro.test.ts`

**Interfaces:**
- Consumes: `AccountRepo`, `AccountInfo` (задача 1); `AccountRuntime` (задача 6); `LeaseLost`
  (задача 4).
- Produces:
  - `app/api/scope.py`:
    - `class EngineRegistry(Protocol)`: `get(account_id: int) -> AccountRuntime | None`;
      `async wait_registered(account_id: int, timeout_s: float) -> AccountRuntime | None`;
      `host_reason(account_id: int) -> str | None`
    - `@dataclass(frozen=True) class AccountScope`: `account: AccountInfo`, `reads: DbReads`,
      `engine: AccountRuntime | None`
    - `async def account_scope(account_id: int, ctx = Depends(current_session), c =
      Depends(container)) -> AccountScope` — нет аккаунта или `owner_id != ctx.admin_id` — 404
      `account not found`
    - `def running(scope = Depends(account_scope)) -> EngineFacade` — 503 `engine not running`
  - `Container`: `db: Database`, `accounts: AccountRepo`, `engines: EngineRegistry`,
    `engine_wait_s: float = 5.0`; поля `facade` и `reads` удаляются.
  - `ConfirmTokens.issue(session_id, account_id, key, params, version)` и
    `check(token, session_id, account_id, key, params, version)`.
  - `app/db/settings_store.py`: `class LeaseHeld(Exception)`; `async def direct_update(db,
    account_id, change: SettingsChange, *, changed_by: str, expected_version: int | None) ->
    tuple[Settings, int]` — одна транзакция: `SELECT lease_holder, lease_expires_at FROM
    accounts WHERE id FOR SHARE`; аренда есть — `LeaseHeld`; версия не та — `SettingsConflict`;
    иначе запись `settings` и `settings_history`.
  - `EngineStatusOut` получает `running: bool`, `status`, `status_reason`, `host_reason`; у
    аккаунта без движка остальные поля — нейтральные (`tg.state = "stopped"`, счётчики 0,
    `pipeline_healthy`/`workers_ok`/`lock_ok` — `false`), режим, пауза и kill — из настроек в
    базе. `TgState.STOPPED = "stopped"`.
  - `app/main.py`: процесс реализует `EngineRegistry` для своего одного аккаунта (до задачи 8).
  - Админка, `src/lib/api/account.ts`:
    - `type AccountPaths` — пути `paths` с префиксом `/api/v1/accounts/{account_id}`, префикс
      срезан, параметр `account_id` убран из `parameters.path`;
    - `createAccountApi(hooks: ApiHooks, accountId: number, fetchImpl?: typeof fetch):
      AccountApi` — клиент `openapi-fetch` `createClient<AccountPaths>` с `baseUrl =
      ${API_ORIGIN}/api/v1/accounts/${accountId}` и теми же middleware, что у `createApi`
      (CSRF, 401, повтор при `csrf token mismatch`; middleware выносятся в общую функцию);
    - `eventsUrl(accountId: number): string`.
    - В местах вызова — относительные пути (`accountApi.GET('/engine/status')`); компоненты,
      которым нужен и глобальный каталог сценариев, получают оба клиента.
    - `errors.ts`: `engine not running` и `engine_starting` — в `ENGINE_DOWN` («Движок
      недоступен»); `account not found` — `not_found`.
    - `app.svelte.ts`: клиент и SSE аккаунта 1 до задачи 14.
  - `tools/login.py`: аргумент `--account <id>` (по умолчанию 1).

- [ ] **Step 1: Тесты доступа** (`tests/api/test_access_matrix.py`, db):

```python
def account_routes(app) -> list[tuple[str, str]]:
    # все (метод, путь) приложения с префиксом /api/v1/accounts/{account_id}
    ...

def test_matrix_covers_spec_table(app) -> None:
    paths = {p for _, p in account_routes(app)}
    for tail in ("/engine/status", "/engine/kill", "/tg/login/start", "/state", "/settings",
                 "/settings/history", "/planner/outlook", "/journal", "/decisions/{decision_id}",
                 "/actions/{action_id}", "/scenario-runs", "/commands/send",
                 "/scenarios/{name}/run", "/metrics", "/metro/runs", "/unrecognized/ack",
                 "/notifications/read", "/daily", "/events"):
        assert f"/api/v1/accounts/{{account_id}}{tail}" in paths

@pytest.mark.parametrize("target", ["foreign", "missing"])
async def test_every_account_route_is_404_for_foreign_or_missing(api, target) -> None:
    # для каждого (метод, путь) из account_routes: учётка admin, аккаунт другой учётки или 999
    # → 404 {"detail": "account not found"}; тело запроса — минимальное валидное
async def test_old_paths_are_gone(api) -> None: ...          # /api/v1/engine/status → 404
async def test_confirm_token_not_valid_on_other_account(api) -> None: ...
```

- [ ] **Step 2: Тесты аккаунта без движка** (`tests/api/test_engine_down.py`, db; реестр —
  фейк с управляемым `wait_registered`):

```python
async def test_status_from_db_when_not_running(api) -> None:
    # running False, status/status_reason из accounts, host_reason из реестра, mode/paused/killed из settings
async def test_state_from_snapshot(api) -> None: ...
async def test_settings_read_and_direct_write(api) -> None:
    # PATCH с верной version → 200, строка settings и settings_history обновлены
async def test_direct_write_version_conflict_409(api) -> None: ...
async def test_direct_write_with_active_lease_goes_to_engine(api) -> None:
    # lease_holder задан, lease_expires_at в будущем; реестр отдаёт движок на wait_registered
    # → правка через facade, прямой записи нет
async def test_direct_write_with_active_lease_and_no_engine_503(api) -> None:
    # engine_wait_s = 0.05 → 503 {"detail": "engine_starting"}, заголовок Retry-After: 2
@pytest.mark.parametrize("path", ["/engine/kill", "/engine/pause", "/tg/login/start",
                                  "/planner/outlook", "/commands/send", "/events"])
async def test_live_routes_503_engine_not_running(api, path) -> None: ...
async def test_sse_streams_only_own_account(api) -> None:
    # реестр с двумя движками (fake): событие в stream аккаунта 2 не приходит в /accounts/1/events
```

И в `tests/db/test_settings_store.py`:

```python
async def test_acquire_waits_for_direct_update(clean_db) -> None:
    # прямая запись держит FOR SHARE; захват аренды (UPDATE accounts) на другом соединении
    # завершается только после её коммита, и движок, прочитавший настройки после захвата, видит запись
async def test_direct_update_refused_with_active_lease(clean_db) -> None: ...   # LeaseHeld
```

- [ ] **Step 3: Тесты админки** (`admin/src/lib/api/account.test.ts`):

```ts
it('подставляет аккаунт в путь', async () => {
  const f = mockFetch(() => json({}));
  await call(createAccountApi(hooks, 7, f).GET('/engine/status'));
  expect(f.calls[0].url).toBe('/api/v1/accounts/7/engine/status');
});
it('шлёт CSRF и повторяет после csrf token mismatch', ...);
it('eventsUrl', () => expect(eventsUrl(7)).toMatch(/\/api\/v1\/accounts\/7\/events$/));
```

Остальные тесты админки — ожидаемые URL с `/api/v1/accounts/1/`.

- [ ] **Step 4: Запустить — падают.**
  `uv run --no-sync pytest tests/api -q`; `cd admin && npm run test`.

- [ ] **Step 5: Реализация** по Interfaces. Все роутеры аккаунта — `APIRouter(prefix=
  "/api/v1/accounts/{account_id}")`, в маршрутах `c.facade` → `running(scope)`, `c.reads` →
  `scope.reads`. `uv run python tools/openapi.py`, `npm run gen:api`; `test_openapi.py` —
  пути с префиксом.

- [ ] **Step 6: Запустить — проходят**, плюс проверки из Global Constraints (включая
  `npm run check` и `npm run build`).

- [ ] **Step 7: Commit** — «API под аккаунтом: все пути аккаунта — в `/api/v1/accounts/{id}`,
  чужой аккаунт — 404; без движка статус, состояние и настройки — из базы, прямая запись настроек
  упорядочена с арендой; токен подтверждения подписывает аккаунт; клиент API аккаунта в админке».

## Часть D. Хост движков

### Task 8: `EngineHost` — все аккаунты в одном процессе

**Files:**
- Create: `app/engine/host/host.py`, `tests/engine/host/test_host.py`
- Modify: `app/main.py`, `app/config.py` (удалить `account_id`), `app/db/base.py` (пул),
  `app/engine/facade.py`, `app/api/routes_engine.py`, `app/api/app.py` (`/readyz`),
  `tests/test_runtime.py`, `tests/test_config.py`, `tests/api/test_response_shapes.py`,
  `openapi.json`, `admin/src/lib/api/schema.d.ts`, админка — места, читающие `lock_ok` и
  `loop_lag_ms` из статуса аккаунта

**Interfaces:**
- Consumes: `LeaseManager`, `Busy` (задача 5); `AccountRuntime`, `RuntimeDeps` (задача 6);
  `EngineRegistry` (задача 7); `AccountRepo` (задача 1).
- Produces (`app/engine/host/host.py`):
  - `@dataclass(frozen=True) class HostStatus`: `holder: str`, `lock_connection_ok: bool`,
    `engines: list[int]`, `busy: dict[int, str]`, `loop_lag_ms: float`, `tasks_ok: bool`
  - `class EngineHost(EngineRegistry)`: `__init__(deps: RuntimeDeps, leases: LeaseManager, *,
    max_engines: int, start_gap_s: float, reconcile_s: float = 30.0, sleep: Callable[[float],
    Awaitable[None]] = asyncio.sleep)`; `async start()` (`adopt_orphans()`, затем все
    `enabled` по возрастанию `id` с паузой `start_gap_s`, затем цикл сверки); `async stop()`
    (штатно все, затем `release`); `get`, `wait_registered`, `host_reason` (реестр);
    `poke() -> None` (разбудить сверку сейчас); `status() -> HostStatus`; `capacity: int`.
  - Сверка желаемого состояния (по `poke` и раз в `reconcile_s`): `enabled` без движка —
    захват и старт; не `enabled` — штатная остановка; поколение движка ≠
    `engine_generation` — перезапуск; `Busy` — `host_reason` (`locked_elsewhere` или
    `lease_active`) и повтор по `retry_in_s`.
  - Один пользователь аренды на аккаунт на хосте: `asyncio.Lock` на аккаунт; новый движок
    стартует, только когда прежний полностью остановлен (п. 3 раздела 4.2).
  - `fence.on_lost` — `abort()` движка; повторный захват — на ближайшей сверке.
  - `on_crash_loop(account_id, task)` — `abort()`, `set_status(error,
    f"crash_loop:{task}")`, уведомление аккаунта `account_crash_loop` (error). Исключение в
    `AccountRuntime.start()` — `set_status(error, f"start_failed:{type(exc).__name__}")`, аренда
    освобождается.
- `app/main.py`: процесс — база, вход, `Container`, `LeaseManager`, `EngineHost`, задачи
  процесса (`session-purge`, `retention`, `lag`, продление аренды). Retention раз в 6 ч (первый
  проход через 5 минут) обходит все аккаунты, кроме `deleting`, с `settings.retention` каждого из
  базы. `PYROBOT_ACCOUNT_ID` в окружении — warning «PYROBOT_ACCOUNT_ID больше не читается».
- `Database(url, *, pool_size: int = 10, max_overflow: int = 20)`.
- Статус аккаунта: `lock_ok` → `lease_ok` (`fence.alive`), `loop_lag_ms` уходит из
  `EngineStatusOut` (есть в `HostStatus`). `/readyz` — 200, когда база отвечает и
  `leases.healthy()`; Telegram не учитывается.

- [ ] **Step 1: Тесты** (`tests/engine/host/test_host.py`, db, `transport="fake"`, `sleep` и
  часы подменены):

```python
async def test_starts_enabled_accounts_in_id_order_with_gap(clean_db) -> None:
    # аккаунты 1, 2 enabled, 3 disabled, 4 error → стартуют 1 и 2, между ними sleep(3.0)
async def test_restart_one_account_leaves_other(clean_db) -> None: ...
async def test_missed_poke_caught_by_periodic_reconcile(clean_db) -> None:
    # repo.restart(1) без poke → через reconcile_s движок 1 нового поколения
async def test_disable_stops_enable_starts(clean_db) -> None: ...
async def test_crash_loop_sets_error_and_notifies(clean_db) -> None:
    # задача движка 1 падает 5 раз → status error, reason "crash_loop:<задача>",
    # уведомление account_crash_loop; движок 2 работает
async def test_start_failure_sets_error_other_accounts_unaffected(clean_db) -> None:
    # старт аккаунта 1 бросает → status error "start_failed:…"; аккаунт 2 работает; после
    # прямой правки настроек и enabled → аккаунт 1 работает, процесс не перезапускался
async def test_two_hosts_one_engine_per_account(clean_db) -> None:
    # хосты A и B на одной базе: каждый аккаунт запущен ровно на одном; у другого host_reason
    # "locked_elsewhere"
async def test_lost_lease_aborts_and_reacquires(clean_db) -> None: ...
async def test_restart_waits_for_previous_runtime(clean_db) -> None:
    # stop() прежнего держится до события → новый start() не вызван до его окончания
async def test_retention_covers_disabled_skips_deleting(clean_db) -> None: ...
```

`tests/test_runtime.py`: `/readyz` 200 без Telegram; warning при `PYROBOT_ACCOUNT_ID`;
`tests/test_config.py`: `account_id` больше нет.

- [ ] **Step 2: Запустить — падают.**

- [ ] **Step 3: Реализация** по Interfaces.

- [ ] **Step 4: Запустить — проходят**, плюс проверки из Global Constraints.

- [ ] **Step 5: Commit** — «Хост движков: все включённые аккаунты в одном процессе с плавным
  стартом и ёмкостью, желаемое состояние из базы со сверкой раз в 30 с, падения одного аккаунта не
  трогают остальные; здоровье процесса отделено от статуса аккаунта».

### Task 9: Аккаунты в API и удаление

**Files:**
- Create: `app/api/routes_accounts.py`, `tests/api/test_accounts_api.py`,
  `tests/engine/host/test_cleanup.py`
- Modify: `app/api/app.py`, `app/db/accounts.py` (`purge`, `overview`),
  `app/engine/host/host.py` (чистка), `openapi.json`, `admin/src/lib/api/schema.d.ts`

**Interfaces:**
- Consumes: `EngineHost` (задача 8), `AccountRepo` (задача 1), `account_scope` (задача 7).
- Produces:
  - `GET /api/v1/accounts` → `list[AccountOut]`: `id`, `name`, `status`, `status_reason`,
    `tg: {user_id, online}`, `mode`, `paused`, `killed`, `last_action_at`, `unread: {warn,
    error}`; `POST /api/v1/accounts` `{name}` → 201 `AccountOut`, 409 `name_taken`,
    409 `capacity_reached`; `PATCH /api/v1/accounts/{account_id}` `{name?, enabled?}` → 200,
    те же 409; `DELETE /api/v1/accounts/{account_id}` `{confirm_name}` → 202, 422
    `confirm_name_mismatch`; `POST /api/v1/accounts/{account_id}/engine/restart` → 202 (движок
    не запущен — 503 `engine not running`); `GET /api/v1/host/status` → `HostStatus`. Каждая
    запись в `accounts` — `host.poke()`. Ёмкость — `config.max_engines`.
  - `AccountRepo.overview(owner_id) -> list[AccountOverview]` (одним запросом: последнее
    действие и непрочитанные warn/error по аккаунтам); `AccountRepo.purge(account_id, *, batch:
    int = 5000) -> None` — строки аккаунта пачками в порядке `DbRetention`, затем
    `tg_sessions`, `tg_peers`, `tg_chat_marks`, `settings`, `settings_history`,
    `state_snapshot`, затем строка `accounts`.
  - `EngineHost` чистит `deleting`: штатная остановка движка с `log_out`; затем захват аренды по
    пп. 1–3; сессия в базе есть, а движка не было — `logout_offline(account_id)` (параметр
    хоста `logout_offline: Callable[[int], Awaitable[None]]`, по умолчанию ничего; реальный — в
    задаче 10) с таймаутом 30 с, сбой — warning в лог; затем `purge`.

- [ ] **Step 1: Тесты**

```python
# tests/api/test_accounts_api.py
async def test_list_only_own_accounts_with_live_fields(api) -> None: ...
async def test_create_201_enabled_dry_run_and_starts(api) -> None: ...
async def test_create_name_taken_and_capacity_reached(api) -> None: ...
async def test_patch_rename_enable_disable(api) -> None: ...
async def test_delete_requires_exact_name(api) -> None: ...     # 422 confirm_name_mismatch
async def test_delete_202_then_gone(api) -> None:
    # после чистки GET /accounts без него, /accounts/{id}/state → 404
async def test_restart_bumps_generation_503_when_not_running(api) -> None: ...
async def test_host_status(api) -> None: ...

# tests/engine/host/test_cleanup.py
async def test_cleanup_removes_every_account_table(clean_db) -> None:
    # во всех таблицах с account_id есть строки аккаунта 2 → после чистки ни одной; аккаунт 1 цел
async def test_cleanup_offline_logout_for_stopped_account_with_session(clean_db) -> None: ...
async def test_cleanup_waits_for_other_host_lease(clean_db) -> None: ...
async def test_cleanup_on_same_host_waits_for_engine_stop(clean_db) -> None: ...
```

- [ ] **Step 2: Запустить — падают.**
- [ ] **Step 3: Реализация** по Interfaces; `uv run python tools/openapi.py`, `npm run gen:api`.
- [ ] **Step 4: Запустить — проходят**, плюс проверки из Global Constraints.
- [ ] **Step 5: Commit** — «Аккаунты в API: список со статусами, создание, переименование,
  включение, удаление с чисткой данных и выходом из Telegram, перезапуск движка аккаунта, статус
  хоста».

## Часть E. Telegram

### Task 10: kurigram на сессии из базы, ограда вызовов, аварийная остановка

**Files:**
- Modify: `app/engine/transport/kurigram.py`, `app/engine/host/account.py`,
  `app/engine/host/host.py`, `app/main.py`, `tests/engine/kurigram_fakes.py`,
  `tests/engine/test_kurigram_transport.py`, `tests/engine/host/test_account_runtime.py`,
  `tests/test_runtime.py`

**Interfaces:**
- Consumes: `PgSessionStorage`, `import_session_file` (задача 3); `SecretBox`, `parse_key`,
  `ensure_key`, `SecretKeyError`, `Undecryptable` (задача 2); `Fence` (задача 4).
- Produces:
  - `KurigramTransport(*, api_id, api_hash, account_id: int, storage: PgSessionStorage, fence:
    Fence, chat_filter: ChatFilter, sink: Sink)` — без `workdir`; клиент —
    `Client(f"account-{account_id}", storage_engine=storage, workers=1, skip_updates=True, …)`.
    Каждый вызов Telegram (`connect`, `send_code`, `sign_in`, `check_password`, `identify`,
    `go_online`, `log_out`, `probe`, `resolve`, `send_text`, `click`, `forward`, `check_group`,
    `fetch`) идёт через `fence.call`.
  - `KurigramTransport.abort() -> None`: `client.session.stop()` (если сессия есть), отмена
    `client.dispatcher.handler_worker_tasks`, `storage.close()` без `save()`; `terminate()` не
    вызывается.
  - `async def logout_offline(db, box, config, account_id) -> None` — временный клиент на
    `PgSessionStorage` только для `auth.LogOut`, затем `storage.delete()`; передаётся в
    `EngineHost(logout_offline=…)`.
  - `RuntimeDeps` получает `box: SecretBox`. `AccountRuntime.start()`: пиры для хранилища —
    чаты из настроек и `swinfo_user_id`; у аккаунта 1 без строки `tg_sessions` и с файлом
    `<data_dir>/pyrobot.session` — `import_session_file`, сбой — уведомление
    `tg_session_import_failed` (warn), файл остаётся; `Undecryptable` при открытии — строки сессии
    удаляются, уведомление `tg_session_unreadable` (error), движок стартует без Telegram.
  - Процесс при старте: `SecretBox(parse_key(config.secret_key))`, `ensure_key(db, box,
    reset=config.secret_key_reset)`; `SecretKeyError` — `log.error` с текстом ошибки и отказ
    старта (исключение из lifespan).

- [ ] **Step 1: Тесты**

```python
# tests/engine/test_kurigram_transport.py (FakeClient; хранилище — фейк с тем же интерфейсом)
async def test_client_uses_storage_engine_and_skips_updates() -> None: ...
async def test_calls_refused_after_fence_deadline() -> None:
    # fence просрочена → send_text/click/forward/fetch бросают LeaseLost, invoke не вызван
async def test_call_cut_when_invoke_hangs_past_deadline() -> None: ...
async def test_abort_stops_session_cancels_handlers_without_terminate() -> None:
    # порядок: session.stop, отмена handler_worker_tasks, storage.close; save и terminate не вызваны;
    # очередь диспетчера не дорабатывается в sink
async def test_logout_offline_logs_out_and_deletes_storage(clean_db) -> None: ...

# tests/engine/host/test_account_runtime.py
async def test_imports_session_file_and_goes_online_without_login(tmp_path, clean_db) -> None:
    # Review Focus 1: pyrobot.session (SQLiteStorage: auth_key, user_id=267519921, пир игры),
    # accounts.tg_user_id = 267519921 → после start() tg ONLINE без входа, строка tg_sessions
    # есть, файл переименован в pyrobot.session.migrated
async def test_broken_session_file_warns_and_keeps_file(tmp_path, clean_db) -> None: ...
async def test_undecryptable_session_dropped_and_reported(clean_db) -> None: ...

# tests/test_runtime.py
@pytest.mark.parametrize("key", [None, "short"])
async def test_process_refuses_without_valid_key(key, caplog) -> None:
    # Review Focus 2: старт падает, в логе «secrets.token_bytes(32)», tg_sessions не тронуты
async def test_process_refuses_with_wrong_key_keeps_sessions(caplog) -> None: ...
async def test_reset_flag_drops_sessions_and_starts(caplog) -> None: ...
```

- [ ] **Step 2: Запустить — падают.**
- [ ] **Step 3: Реализация** по Interfaces.
- [ ] **Step 4: Запустить — проходят**, плюс проверки из Global Constraints.
- [ ] **Step 5: Commit** — «Сессия Telegram аккаунта в базе: kurigram на `PgSessionStorage` без
  своей догонки, перенос `pyrobot.session` без перелогина, проверка ключа при старте; вызовы
  Telegram — в пределах аренды, аварийная остановка без доработки очереди».

### Task 11: Сверка истории

**Files:**
- Create: `app/engine/transport/history.py`, `app/db/chat_marks.py`,
  `app/db/migrations/versions/0013_chat_marks_seed.py`, `tests/engine/test_history_sync.py`,
  `tests/db/test_chat_marks.py`
- Modify: `app/db/journal.py` (`known`), `app/engine/transport/kurigram.py` (источник истории и
  поводы проходов), `app/engine/host/account.py`, `tests/db/test_migrations.py`

**Interfaces:**
- Consumes: `Fence`/`LeaseLost` (задача 4), `KurigramTransport` (задача 10), `to_incoming`,
  `ChatFilter` (`app/engine/transport/kurigram.py`).
- Produces:
  - `Reader = tuple[int, int]` — (`chat_id`, `from_id`; 0 — весь чат);
    `readers_for(chats: ChatsSection) -> set[Reader]`: (игра, 0), (смузи, 0), (приглашения, 0)
    если задан, (`swinfo_chat_id`, `swinfo_user_id`).
  - `ChatMarks(db, account_id, fence: Fence | None)`: `get(reader) -> int | None`;
    `advance(reader, msg_id) -> None` (`INSERT … ON CONFLICT DO UPDATE SET msg_id =
    GREATEST(…)`; без `guard`, но с `fence.check()`); `prune(keep: set[Reader]) -> int`.
  - `DbJournal.known(chat_id: int, keys: Sequence[tuple[int, int, str]]) -> set[tuple[int, int,
    str]]` — какие (`msg_id`, `revision`, `content_hash`) уже есть.
  - `class HistorySource(Protocol)`: `async latest(reader) -> int | None`; `async read(reader,
    above: int, limit: int) -> list[Any]` (сообщения kurigram с `id > above`, не больше
    `limit`, самые новые); `async tail(reader, upto: int, count: int) -> list[Any]`.
    `KurigramTransport` реализует его через `messages.getHistory` (`from_id == 0`) и
    `messages.search` с `from_id`.
  - `class HistorySync`: `__init__(source: HistorySource, marks: ChatMarks, known:
    Callable[[int, Sequence[tuple[int, int, str]]], Awaitable[set[tuple[int, int, str]]]],
    deliver: Callable[[list[IncomingMessage]], Awaitable[bool]], accepts: Callable[[Any],
    bool], notifier: NotifierPort, readers: set[Reader], online: Callable[[], bool], *, limit:
    int = 1000, tail: int = 50, period_s: float = 300.0, backoff: tuple[float, ...] = (30.0,
    60.0, 120.0, 300.0), sleep=asyncio.sleep)`; `request(reason: str) -> None`; `async run() ->
    None`; `async pass_once() -> None`. `deliver` отдаёт сообщения в конвейер и ждёт, что они
    записаны (`True`) — реализация: `submit` каждого, затем `pipeline.drain(60.0)`.
  - Проход — раздел 4.3 спеки: нет отметки — `advance(latest)` без чтения; иначе `read(above=
    отметка, limit + 1)` (больше `limit` — берутся последние `limit` и уведомление
    `history_gap_truncated`, warn) плюс `tail(отметка, 50)`; `to_incoming` и `accepts`; `known`
    отсекает записанное; `deliver`; удача — `advance(самый новый прочитанный msg_id)`. Любое
    исключение (FloodWait, таймаут, `LeaseLost`, потеря входа) — отметка не меняется, повтор по
    `backoff`. Запрос во время прохода — ещё один проход после него. Проходов нет, пока
    `online()` ложно.
  - Поводы в транспорте: конец `go_online` — `request("online")`; `connect_handler` с `session
    is client.session` при онлайне — `request("reconnect")`; подкласс клиента переопределяет
    `handle_updates`: `UpdatesTooLong`, `UpdateChannelTooLong` — `request("gap")`, исключение
    внутри — `request("handle_updates")` и проброс.
  - `AccountRuntime.start()`: `marks.prune(readers_for(chats))`, задача `history` в
    супервизоре аккаунта; старт движка не ждёт прохода.
  - Миграция `0013` (down `0012`): отметки аккаунта 1 — (игра, 0) и (смузи, 0) — наибольший
    `msg_id` чата в `messages`; (`swinfo_chat_id`, `swinfo_user_id`) — наибольший `msg_id`
    сообщений этого `from_id`; id чатов — из `settings.data->'chats'`, по умолчанию значения
    `ChatsSection` на момент миграции (`227859379`, `-1001356300612`, `-1001109615116`,
    `376592453`), вписанные в миграцию. Для чата приглашений отметка не ставится.

- [ ] **Step 1: Тесты** (`tests/engine/test_history_sync.py`, фейковый источник с историей
  чатов, подменённые сон и часы; `tests/db/test_chat_marks.py`, db):

```python
async def test_crash_after_pts_saved_message_journaled_once() -> None: ...
async def test_reconnect_gap_recovered_even_if_live_messages_came_first() -> None: ...
async def test_gap_signals_and_handle_updates_error_request_pass() -> None: ...
async def test_silent_gap_found_by_periodic_pass_within_five_minutes() -> None: ...
async def test_request_during_pass_runs_again_after() -> None: ...
async def test_failed_pass_keeps_mark_and_retries_with_backoff() -> None:
    # Review Focus 5: read бросает FloodWait на старте → отметка прежняя, повтор через 30 с;
    # движок стартовал, не дожидаясь прохода
async def test_filtered_chat_mark_is_chat_head_short_gap_one_request() -> None: ...
async def test_swinfo_read_by_sender_and_shared_invites_chat_has_two_readers() -> None: ...
async def test_only_unknown_or_changed_messages_delivered() -> None: ...
async def test_more_than_limit_takes_last_and_warns() -> None: ...
async def test_no_mark_sets_head_without_reading() -> None: ...
async def test_no_pass_before_online_or_for_non_main_session() -> None: ...
async def test_recovered_flag_on_old_messages() -> None: ...
async def test_marks_only_grow(clean_db) -> None: ...                         # test_chat_marks.py
async def test_prune_removes_readers_not_in_settings(clean_db) -> None: ...   # test_chat_marks.py
async def test_sender_change_starts_new_reader_from_head() -> None: ...
```

И `tests/db/test_migrations.py`:

```python
async def test_0013_seeds_marks_from_journal() -> None:
    # сообщения чата игры 10, 12; swinfo: 50 от swinfo_user и 70 от другого → (игра,0)=12,
    # (swinfo, swinfo_user)=50, у чата приглашений отметки нет
```

- [ ] **Step 2: Запустить — падают.**
- [ ] **Step 3: Реализация** по Interfaces.
- [ ] **Step 4: Запустить — проходят**, плюс проверки из Global Constraints.
- [ ] **Step 5: Commit** — «Сверка истории отслеживаемых чатов: проходы от сохранённой отметки
  при выходе в онлайн, переподключении, разрыве обновлений и раз в 5 минут; swinfo — поиском по
  отправителю; в конвейер — только то, чего нет в журнале».

### Task 12: Перегрузка

**Files:**
- Modify: `app/engine/pipeline.py`, `app/engine/transport/kurigram.py`,
  `app/engine/tg_auth.py`, `tests/engine/test_pipeline.py`,
  `tests/engine/test_kurigram_transport.py`, `tests/engine/kurigram_fakes.py`

**Interfaces:**
- Consumes: `KurigramTransport` (задача 10), `HistorySync.request` (задача 11).
- Produces:
  - `PIPELINE_QUEUE_MAX = 10000`; очередь `Pipeline` — `asyncio.Queue(maxsize=
    PIPELINE_QUEUE_MAX)`, `submit` ждёт места.
  - В `kurigram.py`: `OVERLOAD_HIGH = 5000`, `OVERLOAD_LOW = 500`; `class CountingQueue(
    asyncio.Queue)` подменяет `client.dispatcher.updates_queue` (размер безразмерный);
    транспорт получает `backlog: Callable[[], int]` (очередь конвейера). Сумма очередей выше
    `OVERLOAD_HIGH` — `session.stop()`, `terminate()` (диспетчер разбирает принятое),
    `disconnect()`; `TgState.OVERLOAD`, уведомление `account_overload` (warn). Ниже
    `OVERLOAD_LOW` (проверка раз в 1 с) — новый объект клиента с тем же хранилищем, `connect()`
    и `go_online()` через `fence.call`, затем проход сверки.

- [ ] **Step 1: Тесты**

```python
async def test_overload_stop_order_and_drain() -> None:
    # 5001 обновление в очереди → вызовы session.stop, terminate, disconnect по порядку; всё
    # принятое дошло до sink; статус overload, уведомление account_overload
async def test_resume_below_low_with_new_client_same_storage() -> None:
    # новый объект клиента, storage тот же, send_code не вызывался; HistorySync.request вызван
async def test_history_pass_does_not_reenter_overload() -> None: ...
async def test_pipeline_submit_waits_when_full() -> None: ...                 # test_pipeline.py
```

- [ ] **Step 2: Запустить — падают.**
- [ ] **Step 3: Реализация** по Interfaces.
- [ ] **Step 4: Запустить — проходят**, плюс проверки из Global Constraints.
- [ ] **Step 5: Commit** — «Перегрузка обновлениями: ограниченная очередь конвейера, остановка
  приёма kurigram выше порога и новый клиент ниже него, пропущенное — сверкой истории».

### Task 13: Вход в Telegram — привязка до онлайна, свой чат, лимиты кодов

**Files:**
- Create: `app/engine/host/codes.py`, `tests/engine/host/test_codes.py`
- Modify: `app/engine/tg_auth.py`, `app/engine/host/account.py`, `app/engine/host/host.py`,
  `app/engine/settings.py` (`self_chat_fields`), `app/api/routes_engine.py`,
  `app/api/routes_settings.py`, `app/db/settings_store.py`, `tests/engine/test_tg_auth.py`,
  `tests/api/test_settings_api.py`, `tests/api/test_engine_api.py`, `openapi.json`,
  `admin/src/lib/api/schema.d.ts`, `admin/src/lib/api/errors.ts`

**Interfaces:**
- Consumes: `AccountRepo.bind_telegram`, `TgUserTaken` (задача 1).
- Produces:
  - `self_chat_fields(settings: Settings, tg_user_id: int) -> list[str]` — пути `chats.*`,
    равные `tg_user_id`.
  - `CodeLimiter(per_host: int, per_account: int = 3, *, window_s: float = 3600.0, monotonic=
    time.monotonic)`: `take(account_id) -> float | None` — `None` разрешено, иначе секунды до
    следующей попытки. Один на процесс (`config.tg_codes_per_hour`); `RuntimeDeps` получает
    `codes: CodeLimiter`.
  - `TgAuthManager(backend, *, expected_user_id, bind: Callable[[int], Awaitable[None]],
    self_chat: Callable[[int], list[str]], codes: CodeLimiter | None = None, account_id: int,
    …)`; `class CodeRateLimited(TgAuthError)` с `retry_after_s`. `_accept(user_id)` до
    `go_online`: другой пользователь — `log_out`, `unexpected_user`; не привязан и `bind` дал
    `TgUserTaken` — `log_out`, `tg_user_taken`; `self_chat(user_id)` непуст — привязка остаётся,
    `go_online` не вызывается, состояние `error` с `error="chat_is_self"`, уведомление
    аккаунта `chat_is_self` (warn) с именами полей. `boot()` идёт через тот же `_accept`.
  - `start()` вызывает `codes.take` до `send_code`; отказ — `CodeRateLimited`; маршрут отвечает
    429 `{"detail": "tg_code_rate_limited"}` с `Retry-After` (целые секунды вверх).
  - `PATCH settings` (через движок и прямой записью): при `tg_user_id` аккаунта
    `self_chat_fields` непуст — 422 `{"detail": "chat_is_self", "fields": [...]}`.

- [ ] **Step 1: Тесты**

```python
# tests/engine/test_tg_auth.py
async def test_other_user_rejected_before_online() -> None:      # go_online не вызван, unexpected_user
async def test_user_bound_elsewhere_rejected_before_online() -> None:   # tg_user_taken, log_out
async def test_logout_keeps_binding() -> None: ...
async def test_self_chat_on_first_login_binds_but_stays_offline() -> None:
    # game_chat_id == id входящего, аккаунт не привязан → bind вызван, go_online нет,
    # error "chat_is_self", уведомление с "chats.game_chat_id"; ни одно обновление не в sink
async def test_self_chat_checked_on_boot() -> None: ...
async def test_code_rate_limited() -> None: ...

# tests/engine/host/test_codes.py
def test_three_per_account_ten_per_host_per_hour() -> None: ...

# tests/api/…
async def test_patch_self_chat_422(api) -> None: ...             # через движок и прямой записью
async def test_login_start_429_with_retry_after(api) -> None: ...
```

- [ ] **Step 2: Запустить — падают.**
- [ ] **Step 3: Реализация** по Interfaces; `uv run python tools/openapi.py`, `npm run gen:api`;
  в `errors.ts` — тексты `tg_code_rate_limited`, `chat_is_self`, `tg_user_taken`.
- [ ] **Step 4: Запустить — проходят**, плюс проверки из Global Constraints.
- [ ] **Step 5: Commit** — «Вход в Telegram: привязка и свой чат проверяются до выхода в онлайн,
  пользователь, привязанный к другому аккаунту, не входит; лимит кодов входа на хост и аккаунт».

## Часть F. Админка

### Task 14: Маршруты `/a/[account]`, переключатель и контекст аккаунта

**Files:**
- Move: `admin/src/routes/{+page.svelte,control,daily,journal,metrics,metro,notifications,
  settings,telegram}` → `admin/src/routes/a/[account]/…`
- Create: `admin/src/routes/a/[account]/+layout.svelte`, `admin/src/lib/account.svelte.ts`,
  `admin/src/lib/account.test.ts`, `admin/src/lib/stores/accounts.svelte.ts`,
  `admin/src/lib/stores/accounts.test.ts`, `admin/src/lib/components/AccountSwitcher.svelte`
- Modify: `admin/src/routes/+page.svelte` (новый: перенаправление), `admin/src/routes/
  +layout.svelte`, `admin/src/lib/app.svelte.ts`, `admin/src/lib/nav.ts`, `nav.test.ts`,
  `components/Shell.svelte`, `components/daily/DailyCard.svelte:35`,
  `components/journal/RunSteps.svelte:117`, `routes/a/[account]/metro/+page.svelte` (`goto`)

**Interfaces:**
- Consumes: `createAccountApi`, `eventsUrl` (задача 7); `GET /accounts` (задача 9).
- Produces:
  - `admin/src/lib/account.svelte.ts`: `class AccountContext` — `id`, `api: AccountApi`,
    `live: LiveConnection` (`eventsUrl(id)`), `unread: UnreadCounter`, `engine: EngineStore`,
    `character: CharacterStore`; `start()`, `stop()` (останавливает SSE и опросы; ответы после
    `stop()` никуда не пишутся — у каждого контекста свои экземпляры хранилищ);
    `startAccount(id: number): AccountContext` и `current: { ctx: AccountContext | null }`
    (`$state`) в `app.svelte.ts`; `startApp()` — только сессия, тема, тикер, `AccountsStore`.
  - `AccountsStore(api: Api)`: `list`, `load()`, опрос раз в 30 с и при `visibilitychange` на
    видимую вкладку; `stop()`.
  - `nav.ts`: `accountHref(id: number, path: string): string` (`/a/${id}${path}`), пункты меню —
    функции от `id`; `isActive` учитывает префикс; `NO_RETURN` += `/accounts`; `safeNext` по
    умолчанию `/`; `lastAccount()` / `rememberAccount(id)` — `localStorage` `pyrobot.account` в
    `try/catch`.
  - `routes/a/[account]/+layout.svelte`: `account` из `page.params` (не число или нет в списке
    — `/accounts`); `{#key account}` вокруг содержимого — страничные хранилища (`PlanStore`,
    `DailyStore`, `JournalFeed`, `SettingsEditor`, подписки `live`) создаются заново при смене
    аккаунта; при смене — `current.ctx.stop()`, `startAccount(id)`, `rememberAccount(id)`.
  - `routes/+page.svelte`: `/` → `/a/<lastAccount()>` (если он в списке), иначе первый из списка,
    иначе `/accounts`. Старые пути (`/journal`, `/control`, `/daily`, `/metrics`, `/metro`,
    `/notifications`, `/settings`, `/telegram`) перенаправляются в `/a/<последний>/…` с тем же
    `search` (в корневом `+layout.svelte` до отрисовки).
  - `AccountSwitcher.svelte`: на ПК — вверху бокового меню: имя, точка статуса
    (`enabled`+онлайн / нет), счётчик warn+error; на телефоне — плашка с именем вверху и список
    аккаунтов в «Ещё».

- [ ] **Step 1: Тесты**

```ts
// account.test.ts
it('переключение останавливает SSE прежнего аккаунта и открывает новый', ...);
  // FakeSource: старый закрыт, новый URL /api/v1/accounts/2/events
it('поздний ответ прежнего аккаунта не попадает в новый контекст', ...);
  // Review Focus 3: engine.load аккаунта 1 висит (deferred); переключение на 2; ответ 1 пришёл →
  // ctx2.engine.status — данные аккаунта 2, ctx1 остановлен
it('CharacterStore нового аккаунта принимает снимок с меньшей версией', ...);
// nav.test.ts
it('accountHref и isActive с префиксом', ...);
it('старые пути ведут в последний аккаунт с тем же search', ...);
// accounts.test.ts
it('опрос раз в 30 с и при возвращении на вкладку', ...);
```

Тест страницы: смена `params.account` с `1` на `2` пересоздаёт `JournalFeed` (запрос журнала
уходит на `/api/v1/accounts/2/journal`).

- [ ] **Step 2: Запустить — падают.** `cd admin && npm run test`
- [ ] **Step 3: Реализация** по Interfaces.
- [ ] **Step 4: Запустить — проходят**: `npm run check && npm run test && npm run build`.
- [ ] **Step 5: Commit** — «Админка: экраны аккаунта под `/a/<id>`, переключатель аккаунтов,
  контекст аккаунта пересоздаётся при переключении; старые ссылки ведут в последний аккаунт».

### Task 15: Экран аккаунтов, экран Telegram и аккаунт без движка

**Files:**
- Create: `admin/src/routes/accounts/+page.svelte`,
  `admin/src/lib/components/accounts/AccountsView.svelte`,
  `admin/src/lib/components/accounts/accounts.test.ts`,
  `admin/src/lib/components/EngineDownBanner.svelte`
- Modify: `admin/src/routes/a/[account]/+layout.svelte`,
  `admin/src/lib/components/telegram/TelegramView.svelte`, `telegram/telegram.test.ts`,
  `admin/src/lib/nav.ts` (пункт «Аккаунты»)

**Interfaces:**
- Consumes: `AccountsStore`, `AccountContext` (задача 14); `/accounts` API (задача 9);
  `EngineStatusOut.running/status/status_reason/host_reason`, `TgStatusOut.bound_user_id`.
- Produces:
  - `AccountsView`: таблица (на телефоне — карточки): имя, пользователь Telegram, статус и
    причина, `dry_run`/`live`, пауза и kill, Telegram онлайн, последнее действие, warn/error;
    создание (после 201 — переход на `/a/<id>/telegram`), переименование, включение и
    выключение, удаление — кнопка активна, только когда введённое имя совпало; 409
    `capacity_reached` и `name_taken` — текстом у формы.
  - `EngineDownBanner`: в макете аккаунта, когда `engine.status.running === false`: «Движок не
    запущен: <причина>» (`status_reason`, `host_reason` или статус) и кнопка «Включить»
    (`PATCH /accounts/{id}` `{enabled: true}`) у `disabled` и `error`.
  - `TelegramView`: при `bound_user_id` — «Аккаунт навсегда привязан к пользователю Telegram
    <id>. Другой персонаж — это новый аккаунт»; ошибки `unexpected_user`, `tg_user_taken`,
    `chat_is_self` — понятным текстом.

- [ ] **Step 1: Тесты** (`accounts.test.ts`, `telegram.test.ts`):

```ts
it('список показывает поля аккаунта', ...);
it('создание ведёт на экран Telegram нового аккаунта', ...);
it('удаление требует точного имени и шлёт confirm_name', ...);
it('capacity_reached — текст у формы', ...);
it('плашка «движок не запущен» и включение', ...);
it('экран Telegram объясняет постоянную привязку', ...);
```

- [ ] **Step 2: Запустить — падают.**
- [ ] **Step 3: Реализация** по Interfaces.
- [ ] **Step 4: Запустить — проходят**: `npm run check && npm run test && npm run build`.
- [ ] **Step 5: Commit** — «Админка: экран аккаунтов — создание, переименование, включение и
  удаление; привязка к Telegram на экране входа; плашка аккаунта без движка».

## Часть G. Эксплуатация

### Task 16: CLI `set-password`, окружение и README

**Files:**
- Create: `app/tools/__init__.py`, `app/tools/users.py`, `tests/test_users_tool.py`
- Modify: `README.md`, `.env.example`, `deploy/render-env.sh`, `tests/test_render_env.py`

**Interfaces:**
- Consumes: `AuthRepo` (`get_admin`, `change_password`, отзыв сессий), хэширование паролей из
  `app/api/security.py`.
- Produces: `python -m app.tools.users set-password <login>` — пароль дважды через `getpass`, не
  короче 12 символов (как в API), новый хэш и закрытие всех сессий учётки; неизвестный логин или
  несовпадение — код выхода 1 и строка в stderr.

- [ ] **Step 1: Тесты** (`tests/test_users_tool.py`, db, `getpass` подменён):

```python
async def test_set_password_changes_hash_and_closes_sessions(clean_db) -> None: ...
async def test_set_password_unknown_login_exit_1(clean_db) -> None: ...
async def test_set_password_mismatch_or_short_exit_1(clean_db) -> None: ...
```

- [ ] **Step 2: Запустить — падают.**
- [ ] **Step 3: Реализация**; README:
  - окружение: `PYROBOT_SECRET_KEY` (обязателен, хранить отдельно от бэкапов базы, команда
    генерации), `PYROBOT_SECRET_KEY_RESET`, `PYROBOT_MAX_ENGINES`, `PYROBOT_ENGINE_START_GAP_S`,
    `PYROBOT_DB_POOL_SIZE`, `PYROBOT_DB_MAX_OVERFLOW`, `PYROBOT_TG_CODES_PER_HOUR`; строка
    `PYROBOT_ACCOUNT_ID` удаляется;
  - совет `DELETE FROM admin_users` заменяется на `set-password`;
  - модель аккаунтов: переключатель, привязка к Telegram навсегда, удаление;
  - перенос: что делает миграция, импорт `pyrobot.session`, ручная проверка после выкатки
    (аккаунт 1 онлайн в Telegram без перелогина — `/readyz` Telegram больше не отражает), откат
    (downgrade миграций и переименование `pyrobot.session.migrated` обратно).
- [ ] **Step 4: Запустить — проходят**, плюс проверки из Global Constraints.
- [ ] **Step 5: Commit** — «CLI смены пароля учётки вместо удаления из базы; README: ключ
  шифрования, ёмкость хоста, модель аккаунтов, перенос и откат».

### Task 17: Замер движков

**Files:**
- Create: `tools/bench_engines.py`
- Modify: `README.md` (результат), `app/config.py` (`max_engines` по умолчанию — по результату,
  если он отличается от 20)

**Interfaces:**
- Consumes: `EngineHost`, `AccountRuntime` (задачи 6, 8), фейковый транспорт и фикстуры
  `tests/fixtures`.
- Produces: `uv run python tools/bench_engines.py --engines 5,10,20,40 --minutes 5` — N
  аккаунтов на фейковом транспорте воспроизводят записанный трафик фикстур; печатает таблицу: N,
  RSS МБ, CPU %, задержка цикла p50 и p99 мс. Не в CI.

- [ ] **Step 1: Прогон** на базе из `compose.dev.yml`; ожидается таблица на 4 строки.
- [ ] **Step 2: Решение.** Наибольшее N с p99 < 100 мс — ёмкость по умолчанию
  (`PYROBOT_MAX_ENGINES`); результат и дата — в README.
- [ ] **Step 3: Commit** — «Замер движков в одном процессе и ёмкость хоста по умолчанию».

## Приёмка этапа (раздел 4.9 спеки)

- [ ] Копия прод-базы и `pyrobot.session` на стенде: миграции, старт с новым
  `PYROBOT_SECRET_KEY` — аккаунт 1 онлайн в Telegram без перелогина, журнал и настройки на месте.
- [ ] Владелец создал второй аккаунт, вошёл в Telegram, переключается между аккаунтами; у
  каждого свои журнал, состояние и уведомления.
- [ ] Перезапуск, падение (crash loop) и выход из Telegram одного аккаунта не трогают другой.
- [ ] Замер выполнен (задача 17), ёмкость по умолчанию выставлена и записана в README.
