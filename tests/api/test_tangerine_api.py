from typing import Any

import pytest

from app.db.base import Database
from app.db.settings_store import DbSettingsStore
from app.engine.fence import LeaseLost
from app.engine.settings import ChatIsSelf, SettingsConflict
from app.engine.transport.base import (
    ChatUnavailable,
    FloodWait,
    Sender,
    TransportAuthLost,
    TransportRejected,
)
from app.engine.transport.fake import FakeTransport
from tests.api.conftest import A1, Api, make_user, run_engine
from tests.engine.test_facade import build

pytestmark = pytest.mark.db

TANGERINE = -1001377961602
POST = f"{A1}/tangerine/post"
PAIR = f"{A1}/tangerine/pair"
PARTNER = f"{A1}/tangerine/partner"
ANNA = Sender(42, "Анна", "К", "anna")


async def _engine(
    api: Api, account_id: int = 1, *, authorized: bool = True
) -> tuple[FakeTransport, DbSettingsStore]:
    transport = FakeTransport()
    store = DbSettingsStore(api.db, account_id)
    await store.load()
    f = build(authorized=authorized, transport=transport, settings=store)
    run_engine(api.container, f, account_id)
    await f.tg.boot()
    return transport, store


async def _second(api: Api, name: str = "Второй") -> int:
    owner = await api.container.accounts.get(1)
    assert owner is not None
    return (await api.container.accounts.create(owner.owner_id, name, capacity=10)).id


async def _history(api: Api, account_id: int) -> list[dict[str, Any]]:
    r = await api.client.get(f"/api/v1/accounts/{account_id}/settings/history")
    assert r.status_code == 200
    items: list[dict[str, Any]] = r.json()["items"]
    return items


async def test_post_joins_chat_and_sends_stripped_text(api: Api) -> None:
    transport, _ = await _engine(api)
    assert (await api.client.post(POST, json={"text": "🍊"})).status_code == 403
    r = await api.client.post(POST, headers=api.headers, json={"text": "  привет 🍊 "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chat_id"] == TANGERINE and body["message_id"] > 0
    assert transport.joins == [("mandarinkaSW", TANGERINE)]
    assert transport.posted == [(TANGERINE, "привет 🍊")]


async def test_post_default_text_is_tangerine(api: Api) -> None:
    transport, _ = await _engine(api)
    r = await api.client.post(POST, headers=api.headers, json={})
    assert r.status_code == 200, r.text
    assert transport.posted == [(TANGERINE, "🍊")]


@pytest.mark.parametrize("text", ["", "   ", "я" * 201])
async def test_post_bad_text_is_422(api: Api, text: str) -> None:
    transport, _ = await _engine(api)
    r = await api.client.post(POST, headers=api.headers, json={"text": text})
    assert r.status_code == 422
    assert transport.joins == [] and transport.posted == []


async def test_post_needs_online_telegram(api: Api) -> None:
    transport, _ = await _engine(api, authorized=False)
    r = await api.client.post(POST, headers=api.headers, json={})
    assert (r.status_code, r.json()) == (409, {"detail": "tg_not_online"})
    assert transport.joins == []


async def test_post_chat_mismatch_sends_nothing(api: Api) -> None:
    transport, _ = await _engine(api)
    transport.chat_ids["mandarinkaSW"] = -1001
    r = await api.client.post(POST, headers=api.headers, json={})
    assert (r.status_code, r.json()) == (409, {"detail": "tangerine_chat_mismatch"})
    assert transport.posted == []


@pytest.mark.parametrize(
    ("failure", "code", "detail"),
    [
        (TransportRejected("CHAT_WRITE_FORBIDDEN"), 502, "CHAT_WRITE_FORBIDDEN"),
        (TransportAuthLost("revoked"), 409, "tg_not_online"),
    ],
)
async def test_post_send_errors(api: Api, failure: Exception, code: int, detail: str) -> None:
    transport, _ = await _engine(api)
    transport.fail_with = [failure]
    r = await api.client.post(POST, headers=api.headers, json={})
    assert (r.status_code, r.json()) == (code, {"detail": detail})


async def test_post_flood_wait(api: Api) -> None:
    transport, _ = await _engine(api)
    transport.join_fail_with = [FloodWait(30)]
    r = await api.client.post(POST, headers=api.headers, json={})
    assert (r.status_code, r.json()) == (429, {"detail": "flood_wait"})
    assert r.headers["Retry-After"] == "31"


async def test_post_needs_engine(api: Api) -> None:
    r = await api.client.post(POST, headers=api.headers, json={})
    assert (r.status_code, r.json()) == (503, {"detail": "engine not running"})


async def test_post_refused_for_deleting_account(api: Api) -> None:
    transport, _ = await _engine(api)
    await api.container.accounts.mark_deleting(1)
    r = await api.client.post(POST, headers=api.headers, json={})
    assert (r.status_code, r.json()) == (409, {"detail": "account_deleting"})
    assert transport.joins == []


async def test_pair_posts_both_and_points_each_at_the_other(api: Api) -> None:
    partner = await _second(api)
    mine, my_store = await _engine(api)
    theirs, their_store = await _engine(api, partner)
    before = {1: my_store.version, partner: their_store.version}
    history = {1: await _history(api, 1), partner: await _history(api, partner)}
    body = {"partner_id": partner, "text": " 🍊 "}
    assert (await api.client.post(PAIR, json=body)).status_code == 403
    r = await api.client.post(PAIR, headers=api.headers, json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    my_msg, their_msg = out["account"]["message_id"], out["partner"]["message_id"]
    assert out == {
        "account": {"id": 1, "message_id": my_msg},
        "partner": {"id": partner, "message_id": their_msg},
    }
    assert mine.posted == [(TANGERINE, "🍊")] and theirs.posted == [(TANGERINE, "🍊")]
    assert mine.joins == theirs.joins == [("mandarinkaSW", TANGERINE)]
    # Каждый дарит другому: /gt уходит ответом на сообщение партнёра.
    assert my_store.current.chats.tangerine_reply_to == their_msg
    assert their_store.current.chats.tangerine_reply_to == my_msg
    for account_id, value in ((1, their_msg), (partner, my_msg)):
        version = before[account_id] + 1
        settings = (await api.client.get(f"/api/v1/accounts/{account_id}/settings")).json()
        assert settings["version"] == version
        assert settings["values"]["chats"]["tangerine_reply_to"] == value
        row, *rest = await _history(api, account_id)
        assert rest == history[account_id]
        assert row["version"] == version and row["changed_by"] == "admin"
        assert row["changes"] == {"chats.tangerine_reply_to": [None, value]}


async def test_pair_partner_failure_changes_no_settings(api: Api) -> None:
    partner = await _second(api)
    mine, my_store = await _engine(api)
    theirs, their_store = await _engine(api, partner)
    before = (my_store.version, their_store.version)
    history = (await _history(api, 1), await _history(api, partner))
    theirs.fail_with = [TransportRejected("CHAT_WRITE_FORBIDDEN")]
    r = await api.client.post(PAIR, headers=api.headers, json={"partner_id": partner})
    assert r.status_code == 502
    body = r.json()
    my_msg = body["posted"]["1"]
    assert body == {
        "detail": "tangerine_pair_partial",
        "reason": "CHAT_WRITE_FORBIDDEN",
        "posted": {"1": my_msg},
        "failed": partner,
        "written": [],
    }
    assert mine.posted == [(TANGERINE, "🍊")] and theirs.posted == []
    assert (my_store.version, their_store.version) == before
    assert my_store.current.chats.tangerine_reply_to is None
    assert their_store.current.chats.tangerine_reply_to is None
    assert (await _history(api, 1), await _history(api, partner)) == history


@pytest.mark.parametrize(
    ("failure", "code", "reason"),
    [
        (TransportAuthLost("revoked"), 409, "tg_not_online"),
        (LeaseLost("lease"), 503, "engine not running"),
        (RuntimeError("boom"), 502, "post_failed"),
    ],
)
async def test_pair_partner_failure_reasons(
    api: Api, failure: Exception, code: int, reason: str
) -> None:
    partner = await _second(api)
    mine, my_store = await _engine(api)
    theirs, _ = await _engine(api, partner)
    theirs.fail_with = [failure]
    r = await api.client.post(PAIR, headers=api.headers, json={"partner_id": partner})
    assert r.status_code == code
    body = r.json()
    assert (body["detail"], body["reason"], body["failed"]) == (
        "tangerine_pair_partial",
        reason,
        partner,
    )
    assert list(body["posted"]) == ["1"] and len(mine.posted) == 1
    assert my_store.current.chats.tangerine_reply_to is None


@pytest.mark.parametrize(
    ("failing", "failure", "code", "reason"),
    [
        ("partner", LeaseLost("lease"), 503, "engine not running"),
        ("partner", SettingsConflict("raced"), 409, "version_conflict"),
        ("account", ChatIsSelf(["chats.game_chat_id"]), 422, "chat_is_self"),
        ("account", RuntimeError("db down"), 500, "settings_write_failed"),
    ],
)
async def test_pair_settings_write_failure_keeps_both_message_ids(
    api: Api, failing: str, failure: Exception, code: int, reason: str
) -> None:
    partner = await _second(api)
    mine, my_store = await _engine(api)
    theirs, their_store = await _engine(api, partner)
    theirs._next_id = 5000
    store = their_store if failing == "partner" else my_store
    write = store.update

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise failure

    store.update = broken  # type: ignore[method-assign]
    r = await api.client.post(PAIR, headers=api.headers, json={"partner_id": partner})
    store.update = write  # type: ignore[method-assign]
    assert r.status_code == code
    [(_, my_text)] = mine.posted
    [(_, their_text)] = theirs.posted
    assert my_text == their_text == "🍊"
    body = r.json()
    my_msg, their_msg = body["posted"]["1"], body["posted"][str(partner)]
    assert (my_msg, their_msg) == (1001, 5001)
    failed = partner if failing == "partner" else 1
    assert body == {
        "detail": "tangerine_pair_partial",
        "reason": reason,
        "posted": {"1": my_msg, str(partner): their_msg},
        "failed": failed,
        "written": [1] if failing == "partner" else [],
    }
    # Без отката: записанное остаётся.
    expected = their_msg if failing == "partner" else None
    assert my_store.current.chats.tangerine_reply_to == expected
    assert their_store.current.chats.tangerine_reply_to is None


async def test_pair_partner_flood_wait_keeps_retry_after(api: Api) -> None:
    partner = await _second(api)
    mine, _ = await _engine(api)
    theirs, _ = await _engine(api, partner)
    theirs.join_fail_with = [FloodWait(9)]
    r = await api.client.post(PAIR, headers=api.headers, json={"partner_id": partner})
    assert r.status_code == 429 and r.headers["Retry-After"] == "10"
    body = r.json()
    assert (body["detail"], body["reason"], body["failed"]) == (
        "tangerine_pair_partial",
        "flood_wait",
        partner,
    )
    assert list(body["posted"]) == ["1"] and len(mine.posted) == 1


async def test_pair_first_post_failure_leaves_partner_alone(api: Api) -> None:
    partner = await _second(api)
    mine, _ = await _engine(api)
    theirs, _ = await _engine(api, partner)
    mine.chat_ids["mandarinkaSW"] = -1001
    r = await api.client.post(PAIR, headers=api.headers, json={"partner_id": partner})
    assert (r.status_code, r.json()) == (409, {"detail": "tangerine_chat_mismatch"})
    assert mine.posted == [] and theirs.joins == [] and theirs.posted == []


async def test_pair_needs_both_online_and_says_which(api: Api) -> None:
    partner = await _second(api)
    mine, _ = await _engine(api)
    theirs, _ = await _engine(api, partner, authorized=False)
    r = await api.client.post(PAIR, headers=api.headers, json={"partner_id": partner})
    assert (r.status_code, r.json()) == (409, {"detail": "tg_not_online", "account_id": partner})
    assert mine.joins == [] and mine.posted == [] and theirs.posted == []


async def test_pair_needs_both_engines_and_says_which(api: Api) -> None:
    partner = await _second(api)
    theirs, _ = await _engine(api, partner)
    r = await api.client.post(PAIR, headers=api.headers, json={"partner_id": partner})
    assert (r.status_code, r.json()) == (409, {"detail": "tg_not_online", "account_id": 1})
    mine, _ = await _engine(api)
    other = await _second(api, "Третий")
    r = await api.client.post(PAIR, headers=api.headers, json={"partner_id": other})
    assert (r.status_code, r.json()) == (409, {"detail": "tg_not_online", "account_id": other})
    assert mine.posted == [] and theirs.posted == []


async def test_pair_with_self_is_422(api: Api) -> None:
    mine, _ = await _engine(api)
    r = await api.client.post(PAIR, headers=api.headers, json={"partner_id": 1})
    assert (r.status_code, r.json()) == (422, {"detail": "tangerine_pair_self"})
    assert mine.posted == []


@pytest.mark.parametrize("partner", ["foreign", 999])
async def test_pair_with_foreign_or_missing_partner_is_404(
    api: Api, clean_db: Database, partner: str | int
) -> None:
    mine, _ = await _engine(api)
    if partner == "foreign":
        bob = await make_user(api.container, "bob")
        partner_id = (await api.container.accounts.create(bob, "Боб", capacity=10)).id
        await _engine(api, partner_id)
    else:
        assert isinstance(partner, int)
        partner_id = partner
    r = await api.client.post(PAIR, headers=api.headers, json={"partner_id": partner_id})
    assert (r.status_code, r.json()) == (404, {"detail": "account not found"})
    assert mine.posted == []


@pytest.mark.parametrize("partner_id", [0, 2**31])
async def test_pair_partner_id_out_of_range_is_422(api: Api, partner_id: int) -> None:
    mine, _ = await _engine(api)
    r = await api.client.post(PAIR, headers=api.headers, json={"partner_id": partner_id})
    assert r.status_code == 422
    assert mine.posted == []


async def test_pair_with_deleting_partner_is_409(api: Api) -> None:
    partner = await _second(api)
    mine, _ = await _engine(api)
    await _engine(api, partner)
    await api.container.accounts.mark_deleting(partner)
    r = await api.client.post(PAIR, headers=api.headers, json={"partner_id": partner})
    assert (r.status_code, r.json()) == (409, {"detail": "account_deleting"})
    assert mine.posted == []


async def test_partner_list_is_own_accounts_with_telegram_status(api: Api) -> None:
    # Список для выбора партнёра — GET /accounts: только аккаунты учётки и их Telegram.
    partner = await _second(api)
    await _engine(api)
    await _engine(api, partner, authorized=False)
    bob = await make_user(api.container, "bob")
    await api.container.accounts.create(bob, "Боб", capacity=10)
    rows = (await api.client.get("/api/v1/accounts")).json()
    assert [(a["id"], a["tg"]["online"]) for a in rows] == [(1, True), (partner, False)]


async def _reply_to(api: Api, message_id: int | None, account_id: int = 1) -> None:
    url = f"/api/v1/accounts/{account_id}/settings"
    version = (await api.client.get(url)).json()["version"]
    patch = {"version": version, "changes": {"chats": {"tangerine_reply_to": message_id}}}
    r = await api.client.patch(url, headers=api.headers, json=patch)
    assert r.status_code == 200, r.text


async def _partner(api: Api) -> dict[str, Any]:
    r = await api.client.get(PARTNER)
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


async def test_partner_unset_without_lookup(api: Api) -> None:
    transport, _ = await _engine(api)
    assert await _partner(api) == {
        "reply_to": None,
        "sender": None,
        "account": None,
        "status": "unset",
    }
    assert transport.sender_lookups == []


async def test_partner_foreign_player(api: Api) -> None:
    transport, _ = await _engine(api)
    transport.senders[(TANGERINE, 7)] = ANNA
    await _reply_to(api, 7)
    assert await _partner(api) == {
        "reply_to": 7,
        "sender": {"tg_user_id": 42, "name": "Анна К", "username": "anna"},
        "account": None,
        "status": "ok",
    }


async def test_partner_own_account_by_telegram_user(api: Api) -> None:
    partner = await _second(api)
    await api.container.accounts.bind_telegram(partner, 42)
    transport, _ = await _engine(api)
    transport.senders[(TANGERINE, 7)] = Sender(42, "Анна", None, None)
    await _reply_to(api, 7)
    body = await _partner(api)
    assert body["account"] == {"id": partner, "name": "Второй"}
    assert body["sender"] == {"tg_user_id": 42, "name": "Анна", "username": None}
    assert body["status"] == "ok"


async def test_partner_never_reveals_foreign_owner_account(api: Api, clean_db: Database) -> None:
    bob = await make_user(api.container, "bob")
    bob_acc = (await api.container.accounts.create(bob, "Боб", capacity=10)).id
    await api.container.accounts.bind_telegram(bob_acc, 42)
    transport, _ = await _engine(api)
    transport.senders[(TANGERINE, 7)] = ANNA
    await _reply_to(api, 7)
    body = await _partner(api)
    assert body["account"] is None and body["sender"]["tg_user_id"] == 42


async def test_partner_missing_message(api: Api) -> None:
    await _engine(api)
    await _reply_to(api, 7)
    assert await _partner(api) == {
        "reply_to": 7,
        "sender": None,
        "account": None,
        "status": "missing",
    }


async def test_partner_cached_until_reply_to_changes(api: Api) -> None:
    transport, _ = await _engine(api)
    transport.senders[(TANGERINE, 7)] = ANNA
    transport.senders[(TANGERINE, 9)] = Sender(43, "Борис")
    await _reply_to(api, 7)
    await _partner(api)
    await _partner(api)
    assert transport.sender_lookups == [(TANGERINE, 7)]
    await _reply_to(api, 9)
    body = await _partner(api)
    assert (body["reply_to"], body["sender"]["name"]) == (9, "Борис")
    assert transport.sender_lookups == [(TANGERINE, 7), (TANGERINE, 9)]


async def test_partner_without_engine_is_offline(api: Api) -> None:
    assert (await _partner(api))["status"] == "unset"
    await _reply_to(api, 7)
    assert await _partner(api) == {
        "reply_to": 7,
        "sender": None,
        "account": None,
        "status": "offline",
    }


async def test_partner_telegram_offline(api: Api) -> None:
    transport, _ = await _engine(api, authorized=False)
    await _reply_to(api, 7)
    body = await _partner(api)
    assert (body["reply_to"], body["status"]) == (7, "offline")
    assert transport.sender_lookups == []


@pytest.mark.parametrize(
    "failure",
    [
        FloodWait(30),
        ChatUnavailable(TANGERINE, "PeerIdInvalid"),
        TransportAuthLost("revoked"),
        LeaseLost("lease"),
        RuntimeError("boom"),
        ConnectionError("reset"),
    ],
)
async def test_partner_read_failure_is_offline_and_held(api: Api, failure: Exception) -> None:
    transport, _ = await _engine(api)
    transport.senders[(TANGERINE, 7)] = ANNA
    transport.sender_fail_with = [failure]
    await _reply_to(api, 7)
    offline = {"reply_to": 7, "sender": None, "account": None, "status": "offline"}
    assert await _partner(api) == offline
    # Сбой помнится: следующая загрузка страницы Telegram не спрашивает.
    assert await _partner(api) == offline
    assert transport.sender_lookups == [(TANGERINE, 7)]
