import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.api import overview_battle
from app.api.container import Container
from app.db.models import Account, SettingsRow, StateSnapshot, User
from app.engine.facade import PlannerUnavailable
from app.engine.planner.loop import LoopView, PlanView
from app.engine.settings import Settings
from app.engine.state.model import CharacterState, Obs, Skills, TargetSet, dump_state
from tests.api.conftest import Api, login, make_user

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)  # 12:00 по Москве
B1 = datetime(2026, 10, 17, 10, 0, tzinfo=UTC)  # 13:00 МСК
B2 = datetime(2026, 10, 17, 19, 0, tzinfo=UTC)  # 22:00 МСК
URL = "/api/v1/overview/battle"
HOOLI, UMBRELLA, PIPER = "🤖Hooli", "☂️Umbrella", "📯Pied Piper"
BLACK_MESA, DEFENSE = "☣️Black Mesa", "🛡Защита"


def orders_url(at: datetime | str) -> str:
    return f"{URL}/{at if isinstance(at, str) else at.isoformat()}/orders"


class Frozen:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def monotonic(self) -> float:
        return 0.0


class FakeFacade:
    """Фасад движка для штаба: только `outlook()` с заданным состоянием цикла."""

    stream = None

    def __init__(
        self,
        *,
        ready: str | None = None,
        auto: bool = True,
        current: str | None = None,
        fail: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self._loop = LoopView(
            paused=ready == "paused",
            ready=ready,
            auto=auto,
            current=current,
            current_params=None,
            manual_queue=0,
            next_wake=None,
            wait_reason=None,
            wake_at=None,
        )
        self._fail, self._delay = fail, delay

    async def outlook(self) -> PlanView:
        await asyncio.sleep(self._delay)
        if self._fail is not None:
            raise self._fail
        return PlanView(NOW, cast(Any, None), self._loop)


def run(api: Api, account_id: int = 1, **kw: Any) -> None:
    api.engines.put(cast(Any, FakeFacade(**kw)), account_id)


async def owner_of(c: Container, login_name: str = "admin") -> int:
    async with c.db.sessions() as s:
        uid = await s.scalar(select(User.id).where(User.login == login_name))
    assert uid is not None
    return uid


async def account(
    c: Container,
    account_id: int | None = None,
    *,
    name: str = "acc",
    owner: str = "admin",
    company: str | None = None,
    level: int | None = None,
    skills: tuple[int, int, int, int] | None = None,
    live: bool = True,
    features: dict[str, Any] | None = None,
    overrides: dict[int, str] | None = None,
    target: str | None = None,
    paused: bool = False,
    state: dict[str, Any] | None = None,
    **seen: Any,
) -> int:
    """Аккаунт со снимком: `account_id` None - новый аккаунт учётки `owner`."""
    if account_id is None:
        account_id = (await c.accounts.create(await owner_of(c, owner), name)).id
    fields: dict[str, Any] = dict(seen)
    if company is not None:
        fields["company"] = Obs(value=company, at=NOW)
    if level is not None:
        fields["level"] = Obs(value=level, at=NOW)
    if skills is not None:
        fields["skills"] = Obs(value=Skills(**dict(zip(_SKILLS, skills, strict=True))), at=NOW)
    data = dump_state(CharacterState(**fields)) | (state or {})
    battle: dict[str, Any] = {}
    if target is not None:
        battle["target"] = target
    if overrides is not None:
        battle["overrides"] = overrides
    settings = Settings.model_validate(
        {
            "engine": {"mode": "live" if live else "dry_run"},
            "features": features or {},
            "battle": battle,
        }
    ).model_dump(mode="json")
    settings["engine"]["paused"] = paused
    async with c.db.sessions() as s, s.begin():
        await s.merge(StateSnapshot(account_id=account_id, version=1, state=data))
        await s.merge(SettingsRow(account_id=account_id, version=1, data=settings))
    return account_id


_SKILLS = ("practice", "theory", "cunning", "wisdom")


def seen_battle(at: datetime = B1) -> dict[str, Any]:
    return {"battle_at": Obs(value=at + timedelta(minutes=1), at=NOW)}


async def get(api: Api) -> dict[str, Any]:
    resp = await api.client.get(URL)
    assert resp.status_code == 200, resp.text
    return cast(dict[str, Any], resp.json())


def acct(body: dict[str, Any], account_id: int, battle: int = 0) -> dict[str, Any]:
    [row] = [a for a in body["battles"][battle]["accounts"] if a["id"] == account_id]
    return cast(dict[str, Any], row)


@pytest.fixture(autouse=True)
def frozen(container: Container) -> None:
    container.clock = Frozen(NOW)


async def put(
    api: Api, at: datetime | str, all_: str | None, corps: dict[str, str] | None = None
) -> Any:
    return await api.client.put(
        orders_url(at), json={"all": all_, "corporations": corps or {}}, headers=api.headers
    )


# --- доступ ---


async def test_get_requires_session(api_client: AsyncClient) -> None:
    assert (await api_client.get(URL)).status_code == 401


async def test_put_requires_session_and_csrf(api: Api, api_client: AsyncClient) -> None:
    body = {"all": None, "corporations": {}}
    resp = await api.client.put(orders_url(B1), json=body)
    assert resp.status_code == 403
    api.client.cookies.clear()
    assert (await api.client.put(orders_url(B1), json=body)).status_code == 401


async def test_other_users_accounts_never_listed(api: Api) -> None:
    await make_user(api.container, "bob")
    await account(api.container, name="bobs", owner="bob", company="piper", level=9)
    await account(api.container, 1, company="bmesa", level=31)
    body = await get(api)
    assert [a["id"] for a in body["battles"][0]["accounts"]] == [1]
    assert [c["code"] for c in body["companies"]] == ["bmesa"]


async def test_user_without_accounts_gets_empty_hq(api: Api) -> None:
    await make_user(api.container, "bob")
    await login(api.client, login="bob")
    body = await get(api)
    assert body["companies"] == [] and body["skipped"] == []
    assert body["source"] == "schedule"
    assert [b["accounts"] for b in body["battles"]] == [[], []]
    assert [b["summary"] for b in body["battles"]] == [[], []]


async def test_targets_list_every_choice(api: Api) -> None:
    body = await get(api)
    assert body["targets"] == [
        {"name": PIPER, "code": "piper"},
        {"name": HOOLI, "code": "hooli"},
        {"name": "⚡️Stark Ind.", "code": "stark"},
        {"name": UMBRELLA, "code": "umbrl"},
        {"name": "🎩Wayne Ent.", "code": "wayne"},
        {"name": BLACK_MESA, "code": "bmesa"},
        {"name": DEFENSE, "code": None},
    ]


# --- битвы и источник ---


async def test_schedule_when_nothing_observed(api: Api) -> None:
    await account(api.container, 1, company="bmesa")
    body = await get(api)
    assert body["source"] == "schedule"
    first, second = body["battles"]
    assert (first["battle_at"], first["hour_msk"], first["in_s"]) == (
        "2026-10-17T10:00:00+00:00",
        13,
        3600,
    )
    assert (second["battle_at"], second["hour_msk"]) == ("2026-10-17T19:00:00+00:00", 22)
    assert first["editable"] is True and first["note"] is None


async def test_observed_battle_wins(api: Api) -> None:
    await account(api.container, 1, company="bmesa", **seen_battle(B2))
    body = await get(api)
    assert body["source"] == "observed"
    assert body["battles"][0]["battle_at"] == "2026-10-17T19:00:00+00:00"
    assert body["battles"][1]["battle_at"] == "2026-10-18T10:00:00+00:00"


async def test_changed_schedule_leaves_single_battle(api: Api) -> None:
    odd = datetime(2026, 10, 17, 15, 0, tzinfo=UTC)  # 18:00 МСК
    await account(api.container, 1, company="bmesa", **seen_battle(odd))
    body = await get(api)
    assert [b["hour_msk"] for b in body["battles"]] == [18]


async def test_too_late_not_editable(api: Api, container: Container) -> None:
    container.clock = Frozen(B1 - timedelta(seconds=30))
    await account(container, 1, company="bmesa")
    run(api)
    body = await get(api)
    first = body["battles"][0]
    assert (first["editable"], first["note"]) == (False, "too_late")
    assert body["battles"][1]["editable"] is True
    assert (acct(body, 1)["applies"], acct(body, 1)["note"]) == (False, "too_late")


# --- цель аккаунта ---


async def test_default_target_from_settings(api: Api) -> None:
    await account(api.container, 1, company="bmesa", target=HOOLI)
    run(api)
    row = acct(await get(api), 1)
    assert (row["desired"], row["source"], row["applies"], row["note"]) == (
        HOOLI,
        "default",
        True,
        None,
    )


async def test_override_by_battle_hour(api: Api) -> None:
    await account(api.container, 1, company="bmesa", target=HOOLI, overrides={13: PIPER})
    run(api)
    body = await get(api)
    assert (acct(body, 1)["desired"], acct(body, 1)["source"]) == (PIPER, "override")
    assert (acct(body, 1, 1)["desired"], acct(body, 1, 1)["source"]) == (HOOLI, "default")


async def test_order_all_beats_settings(api: Api) -> None:
    uid = await owner_of(api.container)
    await account(api.container, 1, company="bmesa", target=HOOLI, overrides={13: PIPER})
    await api.container.battle_orders.replace(uid, B1, {"all": UMBRELLA}, "admin")
    run(api)
    body = await get(api)
    assert (acct(body, 1)["desired"], acct(body, 1)["source"]) == (UMBRELLA, "order_all")
    assert acct(body, 1, 1)["source"] == "default"
    assert body["battles"][0]["orders"] == {"all": UMBRELLA, "corporations": {}}


async def test_order_corp_beats_all(api: Api) -> None:
    uid = await owner_of(api.container)
    await account(api.container, 1, company="bmesa")
    await api.container.battle_orders.replace(uid, B1, {"all": HOOLI, "bmesa": PIPER}, "admin")
    run(api)
    body = await get(api)
    assert (acct(body, 1)["desired"], acct(body, 1)["source"]) == (PIPER, "order_corp")
    assert body["battles"][0]["orders"] == {"all": HOOLI, "corporations": {"bmesa": PIPER}}


async def test_all_order_on_own_company_goes_to_defense(api: Api) -> None:
    uid = await owner_of(api.container)
    await account(api.container, 1, company="hooli")
    await api.container.battle_orders.replace(uid, B1, {"all": HOOLI}, "admin")
    run(api)
    row = acct(await get(api), 1)
    assert (row["desired"], row["source"]) == (DEFENSE, "order_all")
    assert (row["applies"], row["note"]) == (True, "own_company")


async def test_all_order_with_unknown_company_not_applied(api: Api) -> None:
    uid = await owner_of(api.container)
    await account(api.container, 1, target=PIPER)
    await api.container.battle_orders.replace(uid, B1, {"all": HOOLI}, "admin")
    run(api)
    row = acct(await get(api), 1)
    assert (row["desired"], row["source"]) == (PIPER, "default")
    assert (row["applies"], row["note"]) == (False, "company_unknown")


# --- дойдёт ли приказ ---


async def test_paused_account_does_not_apply(api: Api) -> None:
    await account(api.container, 1, company="bmesa", paused=True)
    run(api, ready="paused")
    row = acct(await get(api), 1)
    assert (row["applies"], row["note"]) == (False, "paused")


async def test_loop_ready_code_passes_through(api: Api) -> None:
    await account(api.container, 1, company="bmesa")
    run(api, ready="manual_control")
    row = acct(await get(api), 1)
    assert (row["applies"], row["note"]) == (False, "manual_control")


async def test_auto_off(api: Api) -> None:
    await account(api.container, 1, company="bmesa")
    run(api, auto=False)
    row = acct(await get(api), 1)
    assert (row["applies"], row["note"]) == (False, "auto_off")


async def test_running_scenario_still_applies(api: Api) -> None:
    await account(api.container, 1, company="bmesa")
    run(api, current="metro")
    row = acct(await get(api), 1)
    assert (row["applies"], row["note"]) == (True, "scenario_running")


async def test_stopped_engine(api: Api) -> None:
    await account(api.container, 1, company="bmesa")
    row = acct(await get(api), 1)
    assert (row["engine"], row["applies"], row["note"]) == ("stopped", False, "engine_stopped")


async def test_engine_elsewhere(api: Api) -> None:
    await account(api.container, 1, company="bmesa")
    api.engines.reasons[1] = "locked_elsewhere"
    row = acct(await get(api), 1)
    assert (row["engine"], row["applies"], row["note"]) == ("elsewhere", False, "engine_elsewhere")


async def test_dry_run(api: Api) -> None:
    await account(api.container, 1, company="bmesa", live=False)
    run(api)
    row = acct(await get(api), 1)
    assert (row["applies"], row["note"]) == (False, "dry_run")


async def test_feature_off(api: Api) -> None:
    await account(api.container, 1, company="bmesa", features={"battle": False})
    run(api, ready="paused")
    row = acct(await get(api), 1)
    assert (row["applies"], row["note"]) == (False, "feature_off")


async def test_unavailable_outlook_falls_back_to_settings(api: Api) -> None:
    await account(api.container, 1, company="bmesa", paused=True)
    run(api, fail=PlannerUnavailable())
    row = acct(await get(api), 1)
    assert (row["engine"], row["applies"], row["note"]) == ("local", False, "paused")


async def test_slow_outlook_times_out(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overview_battle, "OUTLOOK_TIMEOUT_S", 0.05)
    await account(api.container, 1, company="bmesa")
    run(api, delay=5.0)
    row = acct(await get(api), 1)
    assert (row["engine"], row["applies"]) == ("local", True)


# --- выставлена ли цель ---


async def test_ready_and_in_game_from_profile(api: Api) -> None:
    uid = await owner_of(api.container)
    await account(
        api.container,
        1,
        company="bmesa",
        battle_target=Obs(value=UMBRELLA, at=NOW),
        **seen_battle(B1),
    )
    await api.container.battle_orders.replace(uid, B1, {"all": UMBRELLA}, "admin")
    run(api)
    body = await get(api)
    assert (acct(body, 1)["in_game"], acct(body, 1)["ready"]) == (UMBRELLA, True)
    # На следующую битву цель ещё не ставилась.
    assert (acct(body, 1, 1)["in_game"], acct(body, 1, 1)["ready"]) == (None, None)


async def test_not_ready_when_other_target_in_game(api: Api) -> None:
    await account(
        api.container,
        1,
        company="bmesa",
        target=HOOLI,
        battle_target=Obs(value=UMBRELLA, at=NOW),
        **seen_battle(B1),
    )
    run(api)
    row = acct(await get(api), 1)
    assert (row["in_game"], row["ready"]) == (UMBRELLA, False)


async def test_ready_from_fallback_observation(api: Api) -> None:
    await account(
        api.container,
        1,
        company="bmesa",
        target=HOOLI,
        battle_target_set=Obs(value=TargetSet(target=HOOLI, battle_at=B1), at=NOW),
        **seen_battle(B1),
    )
    run(api)
    row = acct(await get(api), 1)
    assert (row["in_game"], row["ready"]) == (HOOLI, True)


async def test_ready_unknown_without_observations(api: Api) -> None:
    await account(api.container, 1, company="bmesa")
    run(api)
    row = acct(await get(api), 1)
    assert (row["in_game"], row["ready"]) == (None, None)


# --- сводка по целям ---


async def test_summary_groups_by_target(api: Api) -> None:
    uid = await owner_of(api.container)
    c = api.container
    await account(
        c,
        1,
        company="bmesa",
        level=30,
        skills=(30, 20, 10, 5),
        battle_target=Obs(value=UMBRELLA, at=NOW),
        **seen_battle(B1),
    )
    two = await account(c, name="two", company="bmesa", level=20, skills=(1, 2, 3, 4))
    three = await account(c, name="three", company="stark")
    off = await account(c, name="off", company="piper", level=50, features={"battle": False})
    other = await account(c, name="other", company="wayne", level=7, target=HOOLI)
    await c.battle_orders.replace(uid, B1, {"all": UMBRELLA, "wayne": HOOLI}, "admin")
    for i in (1, two, three, off, other):
        run(api, i)
    body = await get(api)
    by_target = {s["target"]: s for s in body["battles"][0]["summary"]}
    umbrella = by_target[UMBRELLA]
    assert umbrella["count"] == 3
    assert umbrella["account_ids"] == [1, two, three]
    assert (umbrella["level_sum"], umbrella["level_avg"], umbrella["unknown"]) == (50, 25.0, 1)
    assert umbrella["skills"] == {"practice": 31, "theory": 22, "cunning": 13, "wisdom": 9}
    assert umbrella["ready"] == 1
    assert by_target[HOOLI]["account_ids"] == [other]
    unmanaged = by_target[None]
    assert (unmanaged["count"], unmanaged["account_ids"]) == (1, [off])
    assert unmanaged["ready"] == 0
    assert body["battles"][0]["summary"][-1]["target"] is None


async def test_summary_average_is_null_without_levels(api: Api) -> None:
    await account(api.container, 1, company="bmesa")
    run(api)
    [row] = (await get(api))["battles"][0]["summary"]
    assert (row["level_sum"], row["level_avg"], row["unknown"], row["count"]) == (0, None, 1, 1)


async def test_companies_counted_among_enabled_accounts(api: Api) -> None:
    c = api.container
    await account(c, 1, company="bmesa")
    await account(c, name="two", company="bmesa")
    await account(c, name="three", company="hooli")
    await account(c, name="nocompany")
    body = await get(api)
    assert body["companies"] == [
        {"code": "hooli", "name": HOOLI, "accounts": 1},
        {"code": "bmesa", "name": BLACK_MESA, "accounts": 2},
    ]


async def test_disabled_accounts_are_skipped(api: Api) -> None:
    c = api.container
    await account(c, 1, company="bmesa")
    off = await account(c, name="off", company="bmesa")
    broken = await account(c, name="broken", company="bmesa")
    async with c.db.sessions() as s, s.begin():
        await s.execute(update(Account).where(Account.id == off).values(status="disabled"))
        await s.execute(update(Account).where(Account.id == broken).values(status="error"))
    body = await get(api)
    assert [a["id"] for a in body["battles"][0]["accounts"]] == [1]
    assert body["skipped"] == [
        {"account_id": off, "reason": "disabled"},
        {"account_id": broken, "reason": "error"},
    ]
    assert body["companies"] == [{"code": "bmesa", "name": BLACK_MESA, "accounts": 1}]


# --- PUT ---


async def test_put_replaces_and_cancels(api: Api) -> None:
    await account(api.container, 1, company="bmesa")
    run(api)
    resp = await put(api, B1, HOOLI, {"bmesa": UMBRELLA})
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    assert saved["battle_at"] == "2026-10-17T10:00:00+00:00"
    assert saved["orders"] == {"all": HOOLI, "corporations": {"bmesa": UMBRELLA}}
    assert (saved["accounts"][0]["desired"], saved["accounts"][0]["source"]) == (
        UMBRELLA,
        "order_corp",
    )
    assert (await get(api))["battles"][0]["orders"] == saved["orders"]
    resp = await put(api, B1, None)
    assert resp.status_code == 200 and resp.json()["orders"] == {"all": None, "corporations": {}}
    assert (await get(api))["battles"][0]["orders"] == {"all": None, "corporations": {}}
    assert acct(await get(api), 1)["source"] == "default"


async def test_put_replaces_whole_battle(api: Api) -> None:
    await put(api, B1, HOOLI, {"bmesa": UMBRELLA})
    await put(api, B1, None, {"piper": HOOLI})
    assert (await get(api))["battles"][0]["orders"] == {
        "all": None,
        "corporations": {"piper": HOOLI},
    }


async def test_put_last_write_wins(api: Api) -> None:
    await put(api, B1, HOOLI)
    resp = await put(api, B1, PIPER)
    assert resp.json()["orders"]["all"] == PIPER
    assert (await get(api))["battles"][0]["orders"]["all"] == PIPER


async def test_put_accepts_zulu_time(api: Api) -> None:
    resp = await put(api, "2026-10-17T10:00:00Z", HOOLI)
    assert resp.status_code == 200 and resp.json()["orders"]["all"] == HOOLI


async def test_put_second_battle(api: Api) -> None:
    await account(api.container, 1, company="bmesa")
    run(api)
    resp = await put(api, B2, HOOLI)
    assert resp.status_code == 200
    body = await get(api)
    assert body["battles"][1]["orders"]["all"] == HOOLI
    assert body["battles"][0]["orders"]["all"] is None


@pytest.mark.parametrize(
    "at",
    [
        "2026-10-17T11:00:00+00:00",
        "2026-10-17T10:00:00",
        "yesterday",
        "2026-10-17T13:00:00+03:00x",
    ],
)
async def test_put_battle_not_offered(api: Api, at: str) -> None:
    resp = await put(api, at, HOOLI)
    assert (resp.status_code, resp.json()) == (422, {"detail": "battle_not_offered"})


async def test_put_msk_offset_for_offered_battle_is_accepted(api: Api) -> None:
    resp = await put(api, "2026-10-17T13:00:00+03:00", HOOLI)
    assert resp.status_code == 200


async def test_put_started_battle_not_offered(api: Api, container: Container) -> None:
    container.clock = Frozen(B1 + timedelta(seconds=1))
    resp = await put(api, B1, HOOLI)
    assert (resp.status_code, resp.json()) == (422, {"detail": "battle_not_offered"})


async def test_put_too_close_at_exact_boundary(api: Api, container: Container) -> None:
    container.clock = Frozen(B1 - timedelta(minutes=1))
    resp = await put(api, B1, HOOLI)
    assert (resp.status_code, resp.json()) == (409, {"detail": "battle_too_close"})
    assert (await get(api))["battles"][0]["orders"]["all"] is None


async def test_put_just_before_boundary_is_accepted(api: Api, container: Container) -> None:
    container.clock = Frozen(B1 - timedelta(minutes=1, seconds=1))
    assert (await put(api, B1, HOOLI)).status_code == 200


@pytest.mark.parametrize(
    "bad", ["☂Umbrella", "Umbrella", "🛡 Защита", "", "☂️Umbrella ", "☣Black Mesa"]
)
async def test_put_rejects_unnormalised_target(api: Api, bad: str) -> None:
    resp = await put(api, B1, bad)
    assert resp.status_code == 422
    [issue] = resp.json()["detail"]
    assert issue["loc"] == ["body", "all"] and issue["type"] == "unknown_target"
    assert (await get(api))["battles"][0]["orders"]["all"] is None


async def test_put_accepts_exact_strings_from_targets(api: Api) -> None:
    for target in [t["name"] for t in (await get(api))["targets"]]:
        resp = await put(api, B1, target)
        assert resp.status_code == 200, target
        assert resp.json()["orders"]["all"] == target


async def test_put_rejects_bad_corporation_target_and_code(api: Api) -> None:
    resp = await put(api, B1, None, {"bmesa": "☣Black Mesa", "zzz": HOOLI})
    assert resp.status_code == 422
    issues = {tuple(i["loc"]): i["type"] for i in resp.json()["detail"]}
    assert issues == {
        ("body", "corporations", "bmesa"): "unknown_target",
        ("body", "corporations", "zzz"): "unknown_company",
    }


async def test_put_rejects_attack_on_own_company(api: Api) -> None:
    resp = await put(api, B1, None, {"hooli": HOOLI})
    assert resp.status_code == 422
    [issue] = resp.json()["detail"]
    assert issue["loc"] == ["body", "corporations", "hooli"]
    assert issue["type"] == "target_is_own_company"


async def test_put_defense_for_own_corporation_is_allowed(api: Api) -> None:
    resp = await put(api, B1, None, {"hooli": DEFENSE})
    assert resp.status_code == 200
    assert resp.json()["orders"]["corporations"] == {"hooli": DEFENSE}


async def test_put_is_scoped_to_owner(api: Api) -> None:
    await make_user(api.container, "bob")
    await account(api.container, 1, company="bmesa")
    run(api)
    await put(api, B1, HOOLI)
    csrf = await login(api.client, login="bob")
    bob = await api.client.put(
        orders_url(B1), json={"all": UMBRELLA, "corporations": {}}, headers={"X-CSRF-Token": csrf}
    )
    assert bob.status_code == 200
    assert (await get(api))["battles"][0]["orders"]["all"] == UMBRELLA
    await login(api.client)
    assert (await get(api))["battles"][0]["orders"]["all"] == HOOLI


async def test_put_wakes_engines_after_commit(api: Api) -> None:
    c = api.container
    await account(c, 1, company="bmesa")
    two = await account(c, name="two", company="bmesa")
    three = await account(c, name="three", company="bmesa")
    gone = await account(c, name="gone", company="bmesa")
    async with c.db.sessions() as s, s.begin():
        await s.execute(update(Account).where(Account.id == gone).values(status="disabled"))
    run(api, 1)
    api.engines.reasons[two] = "locked_elsewhere"
    events: list[str] = []
    replace = c.battle_orders.replace

    async def spy_replace(*args: Any, **kw: Any) -> None:
        await replace(*args, **kw)
        events.append("committed")

    wake = api.engines.orders_changed

    def spy_wake(ids: Any) -> list[int]:
        events.append("woken")
        return wake(ids)

    c.battle_orders.replace = spy_replace  # type: ignore[method-assign]
    api.engines.orders_changed = spy_wake  # type: ignore[method-assign]
    resp = await put(api, B1, HOOLI)
    assert resp.status_code == 200
    assert events == ["committed", "woken"]
    body = resp.json()
    assert body["woken"] == [1]
    assert body["not_woken"] == [
        {"account_id": two, "reason": "locked_elsewhere"},
        {"account_id": three, "reason": "stopped"},
    ]
    assert api.engines.order_wakes == [[1, two, three, gone]]


async def test_rejected_put_wakes_nobody(api: Api) -> None:
    await put(api, "2026-10-17T11:00:00+00:00", HOOLI)
    await put(api, B1, "nonsense")
    assert api.engines.order_wakes == []
