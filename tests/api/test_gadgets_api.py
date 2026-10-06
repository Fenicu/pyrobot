from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.api.container import Container
from app.db.models import LedgerRow, SettingsRow, StateSnapshot
from app.engine.facade import EngineFacade
from app.engine.gadget_catalog import SHOP
from app.engine.gametime import MSK
from app.engine.settings import (
    EngineSection,
    FeaturesSection,
    GadgetsSection,
    GadgetUpgradeSection,
    Settings,
    StaticSettings,
)
from app.engine.state.model import (
    CharacterState,
    GadgetsState,
    GadgetState,
    Obs,
    UpgradeInfo,
    Upgrades,
    dump_state,
)
from tests.api.conftest import A1, Api, run_engine
from tests.engine.test_facade import build

pytestmark = pytest.mark.db
GAD = f"{A1}/gadgets"
LIVE = Settings(engine=EngineSection(mode="live"))
SHOES = SHOP["legs"][5]
PHONE = GadgetState(
    grade="🔴", level=18, slot="📱", name="S-март", bonuses={"practice": 39, "theory": 20}
)
BOOTS = GadgetState(slot="👞", name=SHOES.name, bonuses=dict(SHOES.bonuses), code=SHOES.code)


def snapshot(**over: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    fields: dict[str, Any] = {
        "level": 45,
        "money": 1_120,
        "gadgets": GadgetsState(items=(PHONE, BOOTS), sets=("⚫️Сет VIP",)),
        "bag": 12,
        "bag_cap": 24,
        "upgrades": Upgrades(white=3, blue=4, red=33),
        "upgrade_info": UpgradeInfo(chances={"white": 50, "blue": 70, "red": 90}, confirm=True),
        **over,
    }
    state = CharacterState(**{k: Obs(value=v, at=now) for k, v in fields.items()})
    return dump_state(state)


def task(started_at: datetime, **over: Any) -> GadgetUpgradeSection:
    values: dict[str, Any] = {
        "status": "active",
        "task_id": 3,
        "slot": "right",
        "gadget": "S-март",
        "kind": "auto",
        "target": 25,
        "start_level": 17,
        "started_at": started_at,
        **over,
    }
    return GadgetUpgradeSection(**values)


async def engine(
    api: Api,
    *,
    settings: Settings = LIVE,
    state: dict[str, Any] | None = None,
    online: bool = True,
) -> EngineFacade:
    f = build(settings=StaticSettings(settings.model_copy(deep=True)), snapshot=state)
    await f.pipeline.load()
    run_engine(api.container, f)
    if online:
        await f.tg.boot()
    return f


async def store(container: Container, settings: Settings, state: dict[str, Any]) -> None:
    async with container.db.sessions() as s, s.begin():
        s.add(SettingsRow(account_id=1, version=2, data=settings.model_dump(mode="json")))
        s.add(StateSnapshot(account_id=1, version=5, state=state))


async def attempts(
    container: Container, at: list[datetime], slot: str = "right", first: int = 0
) -> None:
    async with container.db.sessions() as s, s.begin():
        s.add_all(
            LedgerRow(
                account_id=1,
                at=when,
                recorded_at=when,
                day=when.astimezone(MSK).date(),
                kind="gadget_upgrade",
                amounts={"upgrades_red": -1},
                items={f"up:{slot}": 1, "ok" if i % 2 == 0 else "fail": 1},
                chat_id=1,
                msg_id=first + i,
                revision=0,
                content_hash="h",
                seq=0,
            )
            for i, when in enumerate(at)
        )


async def test_get_without_engine_from_settings_and_snapshot(api: Api) -> None:
    settings = Settings(
        features=FeaturesSection(gadgets_buy=True, gorbushka=False, sleep=False),
        gadgets=GadgetsSection(sets=("autumn",), keep_money=1_000),
    )
    await store(api.container, settings, snapshot())
    body = (await api.client.get(GAD)).json()
    phone, boots = body["worn"]
    assert phone == {
        "slot": "📱",
        "up_slot": "right",
        "code": None,
        "name": "S-март",
        "grade": "🔴",
        "level": 18,
        "bonuses": {"practice": 39, "theory": 20},
        "mark": None,
        "set": "summer",
        "shop_tier": None,
    }
    assert (boots["up_slot"], boots["set"], boots["shop_tier"]) == ("legs", None, 6)
    assert body["sets"] == ["⚫️Сет VIP"]
    assert body["bag"] == {"used": 12, "cap": 24}
    assert body["upgrades"] == {"white": 3, "blue": 4, "red": 33}
    assert body["upgrade_info"] == {
        "chances": {"white": 50, "blue": 70, "red": 90},
        "upgrademan_pct": None,
        "confirm": True,
    }
    buy = body["buy"]
    assert buy["enabled"] is True
    assert buy["money"] == {"cash": 1_120, "stocks": 0, "reserve": 1_000, "available": 120}
    plan = buy["plan"]
    # Пустой ⌚️ — тир по карману ($120 доступно); 🍂 Осень без 💍 и 💻 сета — заблокирована.
    assert plan["action"] == {
        "type": "buy",
        "rule": "empty",
        "slot": "left",
        "tier": 2,
        "price": 29,
        "wear": True,
        "sell_needed": 0,
        "in_bag": False,
    }
    assert (plan["verdict"], plan["target"]) == ("chosen", None)
    [autumn] = plan["candidates"]
    assert (autumn["set"], autumn["status"], autumn["blocked_by"]) == (
        "autumn",
        "blocked",
        ["ring", "book"],
    )
    assert autumn["missing"][0] == {"slot": "right", "tier": 12, "price": 39_999}
    assert body["task"]["status"] == "idle" and body["progress"] is None


async def test_get_buy_off_and_unknown_reserve(api: Api) -> None:
    await store(api.container, Settings(), snapshot())
    body = (await api.client.get(GAD)).json()
    assert body["buy"] == {"enabled": False, "plan": None, "money": None}
    # Горбушка включена, её экрана не видели: резерв неизвестен — без плана и суммы.
    on = Settings(features=FeaturesSection(gadgets_buy=True))
    async with api.container.db.sessions() as s, s.begin():
        row = await s.get(SettingsRow, 1)
        assert row is not None
        row.data = on.model_dump(mode="json")
    body = (await api.client.get(GAD)).json()
    assert body["buy"] == {"enabled": True, "plan": None, "money": None}


async def test_get_empty_without_snapshot(api: Api) -> None:
    body = (await api.client.get(GAD)).json()
    assert (body["worn"], body["sets"], body["bag"]) == ([], [], {"used": None, "cap": None})
    assert (body["upgrades"], body["upgrade_info"]) == (None, None)


async def test_get_progress_from_ledger_since_start(api: Api) -> None:
    start = datetime.now(UTC) - timedelta(hours=1)
    before = [start - timedelta(minutes=10), start - timedelta(minutes=5)]
    after = [start + timedelta(minutes=m) for m in (1, 2, 3)]
    await attempts(api.container, before + after)
    await attempts(api.container, [start + timedelta(minutes=4)], slot="left", first=10)
    await store(api.container, Settings(gadget_upgrade=task(start)), snapshot())
    body = (await api.client.get(GAD)).json()
    assert body["task"]["status"] == "active" and body["task"]["level"] == 18
    assert body["progress"] == {
        "attempts": 3,
        "ok": 2,
        "fail": 1,
        "spent": {"white": 0, "blue": 0, "red": 3},
        "level": 18,
    }


async def test_start_and_stop(api: Api) -> None:
    f = await engine(api, state=snapshot())
    started = await api.client.post(
        f"{GAD}/upgrade", headers=api.headers, json={"slot": "right", "target": 25, "kind": "red"}
    )
    assert started.status_code == 202
    out = started.json()
    assert (out["task"]["status"], out["task"]["slot"], out["task"]["target"]) == (
        "active",
        "right",
        25,
    )
    assert (out["task"]["gadget"], out["task"]["start_level"], out["task"]["kind"]) == (
        "S-март",
        18,
        "red",
    )
    assert out["progress"]["attempts"] == 0 and out["progress"]["level"] == 18
    assert f.settings.current.gadget_upgrade.status == "active"
    stopped = await api.client.post(f"{GAD}/upgrade/stop", headers=api.headers)
    assert stopped.status_code == 202
    assert (stopped.json()["task"]["status"], stopped.json()["task"]["end_reason"]) == (
        "stopped",
        "stopped",
    )
    view = (await api.client.get(GAD)).json()
    assert view["task"]["status"] == "stopped"


@pytest.mark.parametrize(
    ("code", "settings", "online", "body"),
    [
        ("tg_not_online", LIVE, False, {}),
        ("dry_run", Settings(), True, {}),
        ("upgrade_in_progress", None, True, {}),
        ("not_worn", LIVE, True, {"slot": "left"}),
        ("target_reached", LIVE, True, {"target": 18}),
    ],
)
async def test_start_conflicts(
    api: Api, code: str, settings: Settings | None, online: bool, body: dict[str, Any]
) -> None:
    if settings is None:
        settings = LIVE.model_copy(update={"gadget_upgrade": task(datetime.now(UTC))})
    await engine(api, settings=settings, state=snapshot(), online=online)
    payload = {"slot": "right", "target": 25, "kind": "auto", **body}
    r = await api.client.post(f"{GAD}/upgrade", headers=api.headers, json=payload)
    assert (r.status_code, r.json()) == (409, {"detail": code})


async def test_stop_without_task_409(api: Api) -> None:
    await engine(api, state=snapshot())
    r = await api.client.post(f"{GAD}/upgrade/stop", headers=api.headers)
    assert (r.status_code, r.json()) == (409, {"detail": "no_task"})


async def test_start_without_engine_503(api: Api) -> None:
    payload = {"slot": "right", "target": 25, "kind": "auto"}
    r = await api.client.post(f"{GAD}/upgrade", headers=api.headers, json=payload)
    assert (r.status_code, r.json()) == (503, {"detail": "engine not running"})
    stop = await api.client.post(f"{GAD}/upgrade/stop", headers=api.headers)
    assert (stop.status_code, stop.json()) == (503, {"detail": "engine not running"})


@pytest.mark.parametrize(
    "body",
    [
        {"slot": "right", "target": 0, "kind": "auto"},
        {"slot": "right", "target": 61, "kind": "auto"},
        {"slot": "hand", "target": 25, "kind": "auto"},
        {"slot": "right", "target": 25, "kind": "gold"},
    ],
)
async def test_body_validation_422(api: Api, body: dict[str, Any]) -> None:
    await engine(api, state=snapshot())
    r = await api.client.post(f"{GAD}/upgrade", headers=api.headers, json=body)
    assert r.status_code == 422
