from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.api import routes_overview_search
from app.db.models import MessageRow, SettingsRow
from app.engine.transport.base import ChatUnavailable, FloodWait, TransportAuthLost
from app.engine.transport.fake import FakeTransport
from app.engine.types import Button
from tests.api.conftest import Api, make_user, run_engine
from tests.api.test_overview_engine_api import account
from tests.engine.helpers import GAME, make_msg
from tests.engine.test_facade import build

pytestmark = pytest.mark.db

URL = "/api/v1/overview/search"
T0 = datetime(2026, 10, 17, 12, 50, 39, tzinfo=UTC)


def ago(minutes: int) -> datetime:
    return T0 - timedelta(minutes=minutes)


_seq = 0


async def add(
    api: Api,
    account_id: int,
    text: str | None,
    *,
    msg_id: int | None = None,
    revision: int = 0,
    when: datetime = T0,
    chat_id: int = GAME,
    outgoing: bool = False,
    markup: dict[str, Any] | None = None,
) -> int:
    global _seq
    _seq += 1
    msg_id = msg_id if msg_id is not None else _seq
    async with api.db.sessions() as s, s.begin():
        s.add(
            MessageRow(
                account_id=account_id,
                chat_id=chat_id,
                msg_id=msg_id,
                revision=revision,
                content_hash=f"h{_seq}",
                kind="edit" if revision else "new",
                date=when,
                received_at=when,
                recovered=False,
                outgoing=outgoing,
                from_id=None,
                text=text,
                markup=markup,
                events=[],
            )
        )
    return msg_id


async def get(api: Api, expect: int = 200, **params: Any) -> Any:
    resp = await api.client.get(URL, params=params)
    assert resp.status_code == expect, resp.text
    return resp.json()


def ids(body: dict[str, Any]) -> list[tuple[int, int]]:
    return [(h["account_id"], h["msg_id"]) for h in body["hits"]]


async def test_requires_session(api_client: AsyncClient) -> None:
    resp = await api_client.get(URL, params={"q": "бой"})
    assert resp.status_code == 401


async def test_db_matches_substring_case_insensitive_newest_first(api: Api) -> None:
    old = await add(api, 1, "Начался БОЙ за город", when=ago(30))
    await add(api, 1, "ничего интересного", when=ago(20))
    new = await add(api, 1, "бой окончен", when=ago(10), outgoing=True)
    body = await get(api, q="  Бой ")
    assert body["q"] == "Бой"
    assert body["where"] == "db"
    assert ids(body) == [(1, new), (1, old)]
    assert body["accounts"] == [
        {"account_id": 1, "name": "Основной", "status": "ok", "reason": None, "count": 2}
    ]
    hit = body["hits"][0]
    assert hit == {
        "account_id": 1,
        "chat_id": GAME,
        "msg_id": new,
        "date": ago(10).isoformat(),
        "outgoing": True,
        "text": "бой окончен",
        "markup": None,
    }


async def test_db_markup_has_journal_shape(api: Api) -> None:
    button = Button("В бой", 0, 0, data="fight").json()
    inline = await add(api, 1, "бой: кнопка", markup={"inline": [button]}, when=ago(2))
    reply = await add(api, 1, "бой: клавиатура", markup={"reply": [["Да", "Нет"]]}, when=ago(1))
    hits = {h["msg_id"]: h["markup"] for h in (await get(api, q="бой"))["hits"]}
    assert hits[inline] == {"inline": [button]}
    assert hits[reply] == {"reply": [["Да", "Нет"]]}


@pytest.mark.parametrize(
    ("needle", "stored", "decoy"),
    [
        ("100%", "скидка 100% на всё", "скидка 1000 на всё"),
        ("a_b", "код a_b рядом", "код axb рядом"),
        ("a\\b", "путь a\\b", "путь ab"),
        ("5%", "ставка 5% налог", "ставка 55 налог"),
    ],
)
async def test_db_wildcards_are_literal(api: Api, needle: str, stored: str, decoy: str) -> None:
    want = await add(api, 1, stored, when=ago(2))
    await add(api, 1, decoy, when=ago(1))
    assert ids(await get(api, q=needle)) == [(1, want)]


async def test_db_only_game_chat_of_the_account(api: Api) -> None:
    other = await account(api.container, "custom")
    async with api.db.sessions() as s, s.begin():
        await s.execute(
            update(SettingsRow)
            .where(SettingsRow.account_id == other)
            .values(data={"chats": {"game_chat_id": 555}})
        )
    game = await add(api, 1, "бой тут", when=ago(1))
    await add(api, 1, "бой в чате команды", chat_id=-100123, when=ago(2))
    own = await add(api, other, "бой в своём чате", chat_id=555, when=ago(3))
    await add(api, other, "бой в чате по умолчанию", chat_id=GAME, when=ago(4))
    assert ids(await get(api, q="бой")) == [(1, game), (other, own)]


async def test_db_limit_is_per_account(api: Api) -> None:
    b = await account(api.container, "b")
    for n in range(5):
        await add(api, 1, f"бой {n}", when=ago(10 + n))
        await add(api, b, f"бой {n}", when=ago(n))
    body = await get(api, q="бой", limit=3)
    counts = {a["account_id"]: a["count"] for a in body["accounts"]}
    assert counts == {1: 3, b: 3}
    assert len(body["hits"]) == 6
    # Ленты аккаунтов склеены по времени, не по аккаунтам.
    dates = [h["date"] for h in body["hits"]]
    assert dates == sorted(dates, reverse=True)
    # Из каждого аккаунта остались самые новые.
    texts = sorted(h["text"] for h in body["hits"] if h["account_id"] == 1)
    assert texts == ["бой 0", "бой 1", "бой 2"]


async def test_db_collapses_to_latest_revision(api: Api) -> None:
    m = await add(api, 1, "бой: ставка 10", revision=0, when=ago(10))
    await add(api, 1, "бой: ставка 20", msg_id=m, revision=5, when=ago(5))
    # Старая ревизия совпадала, а новая уже нет: сообщения в выдаче нет.
    gone = await add(api, 1, "бой скоро", revision=0, when=ago(8))
    await add(api, 1, "всё отменили", msg_id=gone, revision=3, when=ago(4))
    body = await get(api, q="бой")
    assert [(h["msg_id"], h["text"]) for h in body["hits"]] == [(m, "бой: ставка 20")]
    assert body["accounts"][0]["count"] == 1


async def test_db_owner_isolation_and_deleting(api: Api) -> None:
    c = api.container
    await make_user(c, "other")
    foreign = await account(c, "foreign", owner="other")
    gone = await account(c, "gone")
    await c.accounts.mark_deleting(gone)
    mine = await add(api, 1, "бой мой")
    await add(api, foreign, "бой чужой")
    await add(api, gone, "бой удаляемый")
    body = await get(api, q="бой")
    assert ids(body) == [(1, mine)]
    assert [a["account_id"] for a in body["accounts"]] == [1]
    filtered = await get(api, q="бой", account_ids=f"{foreign},{gone},999999,1")
    assert [a["account_id"] for a in filtered["accounts"]] == [1]
    assert ids(filtered) == [(1, mine)]


async def test_db_account_ids_filter(api: Api) -> None:
    b = await account(api.container, "b")
    c = await account(api.container, "c")
    await add(api, 1, "бой раз")
    kept = await add(api, b, "бой два")
    await add(api, c, "бой три")
    body = await get(api, q="бой", account_ids=f"{b}")
    assert ids(body) == [(b, kept)]
    assert [a["account_id"] for a in body["accounts"]] == [b]


async def test_db_account_without_hits_is_listed_with_zero(api: Api) -> None:
    b = await account(api.container, "b")
    await add(api, 1, "бой")
    body = await get(api, q="бой")
    assert {a["account_id"]: a["count"] for a in body["accounts"]} == {1: 1, b: 0}
    assert all(a["status"] == "ok" for a in body["accounts"])


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": " "},
        {"q": "a"},
        {"q": " a "},
        {"q": "я" * 101},
        {"q": "бой", "limit": 0},
        {"q": "бой", "limit": 201},
        {"q": "бой", "where": "nope"},
        {"q": "бой", "account_ids": "1,x"},
    ],
)
async def test_bad_parameters_are_422(api: Api, params: dict[str, Any]) -> None:
    await get(api, 422, **params)


async def test_boundary_lengths_are_accepted(api: Api) -> None:
    await get(api, q="ab")
    await get(api, q="я" * 100, limit=200)


async def test_user_without_accounts_gets_empty_result(api: Api) -> None:
    from tests.api.conftest import login

    await make_user(api.container, "empty_user")
    await login(api.client, login="empty_user")
    for where in ("db", "telegram"):
        body = await get(api, q="бой", where=where)
        assert body["accounts"] == [] and body["hits"] == []


async def test_logs_one_info_line_without_texts(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    await add(api, 1, "секретный бой")
    with caplog.at_level(logging.INFO, logger="app.api.routes_overview_search"):
        await get(api, q="секретный")
    lines = [r for r in caplog.records if r.name == "app.api.routes_overview_search"]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    message = lines[0].getMessage()
    assert "секретный" not in message and "admin" in message and "db" in message


# --- Telegram ---


async def tg_engine(
    api: Api,
    account_id: int,
    *,
    found: list[Any] | None = None,
    authorized: bool = True,
    unreachable: bool = False,
) -> FakeTransport:
    transport = FakeTransport()
    transport.found[GAME] = found or []
    since = datetime(2026, 10, 17, tzinfo=UTC)
    facade = build(
        authorized=authorized,
        transport=transport,
        unreachable_since=(lambda: since) if unreachable else (lambda: None),
    )
    run_engine(api.container, facade, account_id)
    await facade.tg.boot()
    return transport


def by_account(body: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {a["account_id"]: a for a in body["accounts"]}


async def test_telegram_ok_hits_with_markup_sorted(api: Api) -> None:
    b = await account(api.container, "b")
    button = Button("В бой", 0, 0, data="fight")
    first = make_msg("Бой начался", msg_id=10, date=ago(30))
    second = make_msg("Новый бой", msg_id=11, date=ago(5), buttons=(button,), outgoing=True)
    third = make_msg("ещё бой", msg_id=12, date=ago(15))
    t1 = await tg_engine(api, 1, found=[first, second])
    t2 = await tg_engine(api, b, found=[third])
    body = await get(api, q="бой", where="telegram", limit=7)
    assert body["where"] == "telegram"
    assert by_account(body) == {
        1: {"account_id": 1, "name": "Основной", "status": "ok", "reason": None, "count": 2},
        b: {"account_id": b, "name": "b", "status": "ok", "reason": None, "count": 1},
    }
    assert ids(body) == [(1, 11), (b, 12), (1, 10)]
    assert body["hits"][0] == {
        "account_id": 1,
        "chat_id": GAME,
        "msg_id": 11,
        "date": ago(5).isoformat(),
        "outgoing": True,
        "text": "Новый бой",
        "markup": {"inline": [button.json()]},
    }
    assert body["hits"][1]["markup"] is None
    assert t1.searches == [(GAME, "бой", 7)] and t2.searches == [(GAME, "бой", 7)]
    assert t1.sent == [] and t1.posted == []


async def test_telegram_engine_not_running_and_elsewhere(api: Api) -> None:
    away = await account(api.container, "away")
    stopped = await account(api.container, "stopped")
    api.engines.reasons[away] = "locked_elsewhere"
    await tg_engine(api, 1)
    body = await get(api, q="бой", where="telegram")
    rows = by_account(body)
    assert rows[1]["status"] == "ok"
    assert rows[away] == {
        "account_id": away,
        "name": "away",
        "status": "unavailable",
        "reason": "locked_elsewhere",
        "count": 0,
    }
    assert rows[stopped]["status"] == "unavailable"
    assert rows[stopped]["reason"] == "engine_not_running"


async def test_telegram_offline_and_unreachable_are_unavailable(api: Api) -> None:
    offline = await account(api.container, "offline")
    unreachable = await account(api.container, "unreachable")
    t_off = await tg_engine(api, offline, authorized=False)
    t_unr = await tg_engine(api, unreachable, unreachable=True)
    rows = by_account(await get(api, q="бой", where="telegram"))
    assert (rows[offline]["status"], rows[offline]["reason"]) == ("unavailable", "tg_offline")
    assert (rows[unreachable]["status"], rows[unreachable]["reason"]) == (
        "unavailable",
        "tg_unreachable",
    )
    assert t_off.searches == [] and t_unr.searches == []


@pytest.mark.parametrize(
    ("exc", "status", "reason"),
    [
        (FloodWait(30), "error", "flood_wait"),
        (ChatUnavailable(GAME, "ChannelPrivate"), "error", "chat_unavailable"),
        (TransportAuthLost("revoked"), "unavailable", "tg_offline"),
        (ConnectionError("down"), "unavailable", "tg_unreachable"),
        (RuntimeError("boom"), "error", "internal_error"),
    ],
)
async def test_telegram_failures_map_to_codes(
    api: Api, exc: BaseException, status: str, reason: str
) -> None:
    t = await tg_engine(api, 1)
    t.search_fail_with = [exc]
    row = by_account(await get(api, q="бой", where="telegram"))[1]
    assert (row["status"], row["reason"], row["count"]) == (status, reason, 0)


async def test_telegram_one_failure_keeps_other_accounts(api: Api) -> None:
    b = await account(api.container, "b")
    bad = await tg_engine(api, 1)
    bad.search_fail_with = [FloodWait(3)]
    await tg_engine(api, b, found=[make_msg("бой", msg_id=1, date=ago(1))])
    body = await get(api, q="бой", where="telegram")
    rows = by_account(body)
    assert rows[1]["reason"] == "flood_wait" and rows[b]["status"] == "ok"
    assert ids(body) == [(b, 1)]


async def test_telegram_timeout_is_error_for_that_account_only(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes_overview_search, "SEARCH_TIMEOUT_S", 0.05)
    b = await account(api.container, "b")
    slow = await tg_engine(api, 1)

    async def hang() -> None:
        await asyncio.Event().wait()

    slow.on_search = hang
    await tg_engine(api, b, found=[make_msg("бой", msg_id=1, date=ago(1))])
    rows = by_account(await get(api, q="бой", where="telegram"))
    assert (rows[1]["status"], rows[1]["reason"]) == ("error", "timeout")
    assert rows[b]["status"] == "ok" and rows[b]["count"] == 1


async def test_telegram_search_runs_with_bounded_concurrency(api: Api) -> None:
    running = 0
    peak = 0

    async def work() -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.03)
        running -= 1

    ids_ = [1]
    for n in range(6):
        ids_.append(await account(api.container, f"x{n}"))
    for i in ids_:
        t = await tg_engine(api, i)
        t.on_search = work
    body = await get(api, q="бой", where="telegram")
    assert all(a["status"] == "ok" for a in body["accounts"])
    assert peak == routes_overview_search.SEARCH_CONCURRENCY == 3


async def test_telegram_search_does_not_touch_db_messages(api: Api) -> None:
    await add(api, 1, "бой только в базе")
    await tg_engine(api, 1)
    body = await get(api, q="бой", where="telegram")
    assert body["hits"] == [] and cast(int, body["accounts"][0]["count"]) == 0
