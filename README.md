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

## Запуск

```bash
uv run alembic upgrade head
uv run python -m app
```

`app/__main__.py` поднимает uvicorn с одним воркером (`workers=1` — единственный процесс держит и
HTTP, и MTProto-клиент, и очередь действий в одном event loop). `create_application` (`app/main.py`)
собирает `Runtime`, порядок старта: настройки → админ (`ensure_admin`, если задан
`PYROBOT_ADMIN_PASSWORD`) → единственный экземпляр (`app/db/lock.py`, `SingleInstanceLock` — Postgres
advisory lock) → если лок не взят, движок не стартует, уведомление `second_instance`, `/readyz`
навсегда 503 для этого процесса → если остались незавершённые действия с прошлого запуска
(`mark_unfinished_unknown`), шлюз сразу блокирует траты (`block_spending("reconcile_required")`) и
уведомление `actions_outcome_unknown` — сверить исход вручную и снять блок `POST
/api/v1/engine/reconciled` → шина, конвейер (снимок состояния восстанавливается из журнала) →
транспорт (`PYROBOT_TRANSPORT=kurigram|fake`) → шлюз действий, подписанный на шину → `TgAuthManager`
→ фасад → супервизор (`app/engine/supervisor.py`, `Supervisor`) поднимает и перезапускает с
экспоненциальным backoff фоновые задачи конвейера, шлюза, монитора лага event loop, наблюдателя за
локом и (для kurigram) пробы сессии Telegram `tg-probe`, уведомляя `task_failed:<имя>` при падении → `tg.boot()` пытается восстановить существующую
сессию Telegram.

Режим по умолчанию — `dry_run` (`settings.engine.mode`): любое действие, кроме `nav`, подавляется
шлюзом ещё до отправки, в игру ничего не уходит. Переключение в `live` — через настройки движка.

`GET /readyz` отдаёт 200, только когда одновременно: лок держится этим процессом, все фоновые
задачи супервизора живы, Telegram в состоянии `ONLINE`, kill switch не активен, блок трат снят и
конвейер здоров. Фоновая задача `lock-watch` каждые 10 секунд перепроверяет, что advisory lock
по-прежнему держит именно текущее backend-соединение — SQLAlchemy может молча переподключиться
после разрыва, и обычный `SELECT 1` прошёл бы на новом соединении, потеряв лок незаметно. При
потере — `kill("lock_lost")` (latch, без сохранения в настройки) и ровно одно уведомление
`lock_lost`, после чего проверка лока не повторяется (задача не завершается и не перезапускается
супервизором — она паркуется, чтобы не слать `lock_lost` повторно на каждом цикле backoff);
`/readyz` уходит в 503. Автоматического восстановления нет, нужен рестарт процесса: `POST
/api/v1/engine/unkill` без лока отвечает 409 `{"detail": "lock_lost"}` и latch не снимает, а шлюз
независимо от latch отклоняет любую отправку, пока лок не держится (предикат `can_send`, см. ниже).

Остановка (`Runtime.stop`) идёт по независимым шагам: закрыть шлюз → остановить супервизор →
остановить транспорт → освободить lock → закрыть пул БД — сбой одного шага не мешает остальным.

## Структура

- `app/engine` — движок: типы сообщений, парсеры, реестр команд, конвейер, шлюз действий, транспорт.
- `app/db` — Postgres: модели, миграции, хранилища.
- `app/api` — HTTP API для админки.

Транспорт kurigram (`app/engine/transport/kurigram.py`) держит `workers=1` — апдейты обрабатываются
строго по порядку — и `skip_updates=False`, чтобы при старте догнать пропуски; identity (`get_me`)
проверяется до запуска апдейтов. Исходящие RPC (`SendMessage`, `GetBotCallbackAnswer`) — одна попытка
без сна и повторов SDK, все повторы и паузы идут только через шлюз действий. Потеря авторизации
(401 от Telegram) обнаруживается при отправке текста, при клике и пробой `probe()` — `updates.GetState`
раз в 60 секунд (фоновая задача `tg-probe`, `Runtime.tg_probe_s`, пробует только в состоянии
`ONLINE`). Во всех трёх случаях клиент принудительно останавливается (ошибки остановки логируются, но
не мешают), файл сессии удаляется и создаётся новый клиент — повторный вход идёт с чистого состояния,
после чего вызывается `on_auth_lost`; отправка и клик дополнительно поднимают `TransportAuthLost`.
Прочие ошибки пробы только логируются. `log_out()` при любом исходе `auth.LogOut` делает ту же
очистку: 401 (сессия уже отозвана) считается успешным выходом, другая ошибка пробрасывается после
очистки. `BadRequest` при клике — `TransportRejected`; если бот
не прислал тост (таймаут ожидания ответа или `BOT_RESPONSE_TIMEOUT` от Telegram — игра часто выполняет
действие без тоста), `click()` возвращает `None`, и исход проверяется ожиданием шлюза, а не считается
отказом. Таймаут тоста `click_answer_timeout_s` — не больше 30 секунд. Фильтр чатов (`ChatFilter`) пропускает: игровой чат и
канал смузи — любые сообщения; чат SWINFO — только от пользователя SWINFO; чат приглашений в бой —
только сообщения с кнопкой `join_fight_<11 символов>` (один текст без кнопки не считается); всё
остальное отбрасывается ещё до журнала. Наивные даты pyrogram трактуются как локальные и переводятся
в UTC; сообщение помечается `recovered`, если разрыв между получением и датой сообщения больше 60 секунд.

Вход в Telegram (`app/engine/tg_auth.py`, `TgAuthManager`) сериализует все шаги логина одним
`asyncio.Lock`. `boot()` при уже авторизованной сессии проверяет identity **до** запуска апдейтов:
чужой аккаунт получает `log_out()` и состояние `ERROR "unexpected_user"`, апдейты не стартуют.
Попытка входа (`start`/`submit_code`/`submit_password`) привязана к `owner` и `attempt_id` с TTL
(`attempt_ttl_s`, по умолчанию 600 с): активную непросроченную попытку другого владельца отклоняет
`AttemptMismatch`, тот же владелец может начать заново, после истечения TTL попытку может подхватить
кто угодно. Коды и пароли никогда не попадают в текст ошибок и логи. `mark_lost()` (колбэк
`on_auth_lost` транспорта) переводит менеджер в `UNAUTHORIZED` с `error="session_revoked"`, если
сессия отозвана извне; при переходе из `ONLINE` отправляется одно уведомление `error` с кодом
`tg_auth_lost` (повторная потеря без нового входа уведомление не дублирует). Шлюз с этого момента
отклоняет отправку (`tg_offline`); восстановление — повторный вход в Telegram через админку
(`/api/v1/tg/login/*`). `on_online()` регистрирует колбэки, вызываемые после успешного входа.

Конвейер входящих (`app/engine/pipeline.py`, `app/engine/bus.py`) обрабатывает обновления строго
последовательно: дедуп по ревизии, журнал и снимок состояния пишутся в одной транзакции, публикация
подписчикам — только после фиксации. Сбой журнала (БД) повторяется с backoff без потери и без
переупорядочивания сообщений; ошибка редьюсера не теряет сообщение. Догнанные старые сообщения
(`recovered`) старше `react_max_age` помечаются `reactable=False` — только журнал и снимки, без реакций.
Подписчики шины не должны ждать результатов действий — только быстро перекладывать доставку дальше.

Шлюз действий (`app/engine/gateway`) — единственный путь отправки: любой текст или клик по кнопке
проходит через `ActionGateway`. Команды разбиты на классы (`app/engine/commands.py`): `nav`
(навигация), `action` (игровые действия), `risky` (необратимые), `forbidden`/`donate` (не
отправляются никогда). Любая трата Sw-coin (🌐) — `donate`, включая бафы метро за монеты
(`maze_buf_coins_*`), весенние розыгрыши и смену призов за 🌐 (`spring_roll_coins*`,
`spring_regenerate*`) и весенние призы `/sbN`. Callback метро и весны принимаются только по белому
списку реально встреченных значений (`maze_up`, `maze_exit_accept`, `maze_buf_tokens_*`, …,
`spring_roll_smiles`); любое другое значение, как и неизвестная команда, — `forbidden`. Правила проверяются при постановке в очередь, при выборе и перед каждой
попыткой отправки: `risky` уходит только при `source=MANUAL` и подтверждённом `risky_confirmed`,
иначе `REJECTED "risky_requires_confirm"`; `action`/`risky` без `Expectation` отклоняются
(`REJECTED "expectation_required"`) — успешный вызов API сам по себе не подтверждает результат в
игре, подтверждает только ответ игры по предикату; только `nav` без ожидания подтверждается сразу
после отправки (`CONFIRMED "sent"`). Kill switch (ручной `kill()` или `settings.engine.killed`)
подавляет всё (`SUPPRESSED "kill_switch"`), `dry_run` подавляет всё, кроме `nav`
(`SUPPRESSED "dry_run"`), блок трат (`block_spending`) отклоняет всё, кроме `nav`
(`REJECTED "blocked:<reason>"`). Предикат `can_send` (из `Runtime`) проверяется там же, где kill
switch и `dry_run`, и отклоняет любую команду, включая `nav`: без single-instance лока — `REJECTED
"lock_lost"`, если Telegram не в состоянии `ONLINE` — `REJECTED "tg_offline"`. Истёкший TTL — `REJECTED "expired"`, клик по кнопке вне последней
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

## API

`app/api` (`create_api(container) -> FastAPI`) — HTTP API админки. Авторизация — сессионная cookie
`pyrobot_session` (`Secure` в боевой конфигурации, `HttpOnly`, `SameSite=Strict`), срок жизни 30 дней
со скольжением: если с последнего обращения прошли сутки, срок и сама cookie продлеваются заново при
следующем запросе. Пароль хранится как argon2-хэш (`argon2-cffi`), хэширование и проверка выполняются
в threadpool не более чем в 2 параллельных потока (`LoginRateLimiter.slots`). `POST
/api/v1/auth/login` принимает `{login, password}` и в ответ отдаёт `{login, csrf_token}` вместе с
cookie; мутирующие запросы (`POST /api/v1/auth/logout`, `POST /api/v1/auth/password`) требуют
заголовок `X-CSRF-Token` с токеном текущей сессии, иначе 403. Попытки входа с одного IP
сериализуются (`LoginRateLimiter.lock_for`) и ограничены экспоненциальным backoff: 5 бесплатных
попыток, дальше — 429 с заголовком `Retry-After`, растущим от `base_s` до `max_s`. Смена пароля
(`POST /api/v1/auth/password`, новый пароль не короче 12 символов) отзывает все сессии админа,
включая текущую. `GET /healthz` — проверка живости, без авторизации.

`EngineFacade` (`app/engine/facade.py`) — фасад над `ActionGateway`, `Pipeline` и `TgAuthManager`:
`status()` отдаёт режим, kill switch, блок трат, статус Telegram, длину очереди, текущее действие,
бэклог и здоровье конвейера, `lock_ok`/`workers_ok` и лаг event loop (`LoopLagMonitor`,
`app/engine/lag.py`, максимум лага за скользящее окно 60с); `ready()` — истина, когда лок и воркеры
в порядке, Telegram в состоянии `ONLINE`, нет kill switch, нет блока трат и конвейер здоров.
`GET /api/v1/engine/status` (сессия) отдаёт этот статус целиком. `POST /api/v1/engine/kill {reason}`
(CSRF) сперва латчит `ActionGateway` и обрывает очередь, затем сохраняет настройку — если сохранение
не удалось, latch остаётся активным. `POST /api/v1/engine/unkill` (CSRF) — в обратном порядке:
сначала сохраняет настройку, затем снимает latch; если сохранение падает, шлюз остаётся выключенным
и исключение уходит наверх; если single-instance лок потерян — 409 `{"detail": "lock_lost"}`, ни
настройка, ни latch не меняются. `POST /api/v1/engine/reconciled` (CSRF) снимает блок трат после сверки.
`GET /api/v1/tg/status` (сессия) и `POST /api/v1/tg/login/start {phone}`, `.../login/code
{attempt_id, code}`, `.../login/password {attempt_id, password}`, `POST /api/v1/tg/logout` (все —
CSRF) проксируют `TgAuthManager`; несовпадение попытки входа (`AttemptMismatch`) отдаёт 409.
`GET /readyz` (без авторизации) — 200 `{"status":"ready"}`, если движок поднят и `ready()` истинна,
иначе 503 `{"status":"not_ready"}`.
