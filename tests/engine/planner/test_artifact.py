from datetime import datetime, timedelta
from typing import Any

from app.engine.planner.base import TIMER_MARGIN
from app.engine.planner.decide import NextDeed, decide, outlook
from app.engine.planner.types import Act, Wakeup
from app.engine.scenarios.registry import CERTIFIED
from app.engine.settings import ArtifactRunSection, ArtifactsSection, Settings
from app.engine.state.model import (
    ArtifactCollect,
    BusyState,
    GorbushkaState,
    Obs,
    PriceState,
    StartupState,
)
from tests.engine.planner.test_daily import DAILY, offers, picked, tasks
from tests.engine.planner.test_decide import BASE, NOW, act, awake, config, m, verdicts, w
from tests.engine.planner.test_obligations import (
    METRO_ALONE,
    METRO_PARAMS,
    NOON,
    PHASE4,
    metro_state,
    msk,
    run_in,
    state,
)

END = m(9 * 24 * 60)


def collect_run(
    artifact: str = "light",
    status: str = "active",
    started: datetime | None = None,
    ends: datetime = END,
) -> ArtifactRunSection:
    return ArtifactRunSection.model_validate(
        {"artifact": artifact, "status": status, "started_at": started or m(-60), "ends_at": ends}
    )


def mode(settings: Settings, **run: Any) -> Settings:
    return settings.model_copy(update={"artifact_run": collect_run(**run)})


def seen(artifact: str = "light", at: datetime = NOW) -> Obs[ArtifactCollect]:
    return Obs(value=ArtifactCollect(artifact=artifact, ends_at=END), at=at)


def test_light_collect_spends_motivation_on_walks_only() -> None:
    decision = decide(awake(artifact_collect=seen()), mode(BASE), NOW)
    assert act(decision) == ("deed:walk", {})
    assert isinstance(decision, Act) and decision.reason == "artifact light"
    # Горбушка включена в настройках, но в сборе бой 🔥 не получает.
    assert verdicts(decision) == {
        "gorbushka": "artifact_run",
        "deed:harvest": "artifact_run",
        "deed:job": "artifact_run",
        "deed:learn": "artifact_run",
        "deed:dconv": "artifact_run",
        "deed:walk": "chosen",
    }


def test_startup_does_not_pill_during_artifact_run() -> None:
    # Во время сбора 🔥 идут на дела тактики: «Пилить» — отказ `artifact_run`, без /dos.
    screen = Obs(value=StartupState(level=6, max=False, progress=0, progress_needed=1200), at=NOW)
    state = awake(raw=5, startup=screen, artifact_collect=seen())
    cfg = mode(
        BASE.model_copy(update={"features": BASE.features.model_copy(update={"startup": True})})
    )
    decision = decide(state, cfg, NOW)
    assert verdicts(decision)["deed:startup"] == "artifact_run"
    assert act(decision)[0] != "deed:startup"


def test_tactic_order_is_strict_and_book_follows_character_level() -> None:
    cfg = mode(
        BASE.model_copy(update={"artifacts": ArtifactsSection(book_low=("job", "walk"))}),
        artifact="book",
    )
    young = awake(level=17, artifact_collect=seen("book"))
    assert act(decide(young, cfg, NOW)) == ("deed:job", {})
    # Работа не по карману 🔥 — следующая по порядку, а не лучшая по оценке.
    pricey = young.model_copy(
        update={"prices": {"job": Obs(value=PriceState(motivation=99, minutes=2), at=NOW)}}
    )
    assert act(decide(pricey, cfg, NOW)) == ("deed:walk", {})
    grown = awake(level=18, artifact_collect=seen("book"))
    assert act(decide(grown, cfg, NOW)) == ("deed:learn", {})


def test_collect_turns_off_gorbushka_metro_and_their_reserves() -> None:
    flags = {"features": {"metro": True, "gorbushka": True}}
    fight = GorbushkaState(state="meeting", won=1, total=4, next_fight_at=m(10), fight_cost=1)
    current = awake(motivation=1, gorbushka=fight, artifact_collect=seen())
    decision = decide(current, mode(config(flags)), NOW)
    assert act(decision) == ("deed:walk", {})
    found = verdicts(decision)
    assert (found["gorbushka"], found["metro"]) == ("artifact_run", "artifact_run")
    assert outlook(current, mode(config(flags)), NOW).reserves == ()
    # Без сбора та же 🔥 держится под бой Горбушки и вход в метро.
    assert verdicts(decide(current, config(flags), NOW))["deed:walk"] == "reserved"


def test_metro_resume_still_runs_while_collecting() -> None:
    inside = run_in(NOON - timedelta(minutes=3))
    s = metro_state(
        NOON,
        metro_message=inside,
        metro_ready_at=NOON + timedelta(hours=16),
        motivation=0,
        artifact_collect=seen(at=NOON),
    )
    decision = decide(s, mode(METRO_ALONE, started=NOON - timedelta(hours=1)), NOON)
    assert act(decision) == ("metro", {**METRO_PARAMS, "resume": 3624441})


def test_eat_before_battle_allowed_while_collecting() -> None:
    now = msk(12, 40)
    cfg = Settings.model_validate(
        {"features": {**dict.fromkeys(PHASE4, False), "fastfood": False}}
    )
    hungry = state(
        now,
        stamina=0,
        battle_at=msk(13),
        artifact_collect=Obs(
            value=ArtifactCollect(artifact="light", ends_at=now + timedelta(days=9)), at=now
        ),
    )
    collect_cfg = mode(cfg, started=now - timedelta(hours=1), ends=now + timedelta(days=9))
    assert act(decide(hungry, collect_cfg, now)) == ("deed:eat", {})


def test_daily_pick_only_tasks_done_by_tactic_deeds() -> None:
    current = tasks(
        offers("convDets_hard", "robPro_hard", "materials_hard", "walkMoney_hard"),
        artifact_collect=seen(),
    )
    decision = decide(current, mode(DAILY), NOW)
    assert picked(decision)[:2] == ("daily_pick", {"task": "materials_hard"})
    rejected = {(c.params.get("task"), c.verdict) for c in decision.candidates}
    assert {("convDets_hard", "artifact_run"), ("robPro_hard", "artifact_run")} <= rejected
    only_bad = tasks(offers("convDets_hard", "robPro_hard"), artifact_collect=seen())
    decision = decide(only_bad, mode(DAILY), NOW)
    assert ("daily_pick", {}, "artifact_run") in [
        (c.scenario, c.params, c.verdict) for c in decision.candidates
    ]
    assert act(decision) == ("deed:walk", {})


def test_screen_reread_after_start_and_every_three_hours() -> None:
    cfg = mode(BASE, started=m(-600))
    assert act(decide(awake(), cfg, NOW)) == ("refresh", {"source": "artifacts"})
    old = awake(artifact_collect=seen(at=m(-181)))
    assert act(decide(old, cfg, NOW)) == ("refresh", {"source": "artifacts"})
    before_start = awake(artifact_collect=seen(at=m(-61)))
    assert act(decide(before_start, mode(BASE), NOW)) == ("refresh", {"source": "artifacts"})
    fresh = awake(artifact_collect=seen(at=m(-60)))
    assert act(decide(fresh, cfg, NOW)) == ("deed:walk", {})
    view = outlook(fresh, cfg, NOW)
    assert Wakeup(w(120), "refresh", "artifacts") in view.wakeups
    assert Wakeup(END + TIMER_MARGIN, "artifact_end") in view.wakeups
    assert view.hints.next_deed == NextDeed("deed:walk", "artifact")


def test_paused_or_finished_collect_plays_as_usual() -> None:
    usual = act(decide(awake(), BASE, NOW))
    assert act(decide(awake(artifact_collect=seen()), mode(BASE, status="paused"), NOW)) == usual
    over = mode(BASE, started=m(-14400), ends=m(-1))
    decision = decide(awake(artifact_collect=seen()), over, NOW)
    assert act(decision) == usual
    assert "artifact_run" not in verdicts(decision).values()


def test_start_requested_runs_when_character_is_free() -> None:
    run = ArtifactRunSection.model_validate(
        {"artifact": "light", "status": "starting", "requested_at": m(-1)}
    )
    cfg = BASE.model_copy(update={"artifact_run": run})
    decision = decide(awake(), cfg, NOW, certified=CERTIFIED)
    assert act(decision) == ("artifact_start", {"artifact": "light"})
    assert isinstance(decision, Act) and decision.reason == "artifact_starting"
    busy = awake(busy=BusyState(activity="job", until=m(3)))
    assert verdicts(decide(busy, cfg, NOW, certified=CERTIFIED))["artifact_start"] == "busy"


def test_end_wakeup_only_for_active_collect() -> None:
    fresh = awake(artifact_collect=seen(at=m(-60)))
    end = Wakeup(END + TIMER_MARGIN, "artifact_end")
    assert end in outlook(fresh, mode(BASE), NOW).wakeups
    # Конец приостановленного сбора закроет `ArtifactRuns.tick` на ближайшем шаге цикла.
    paused = outlook(fresh, mode(BASE, status="paused"), NOW)
    assert all(t.kind != "artifact_end" for t in paused.wakeups)


def test_inside_metro_while_collecting_waits_for_kick() -> None:
    # Сбор продолжен, пока персонаж в метро, а продолжать забег поздно: входа нет, но цикл
    # проснётся к выбросу.
    entered = NOON - timedelta(hours=3)
    s = metro_state(
        NOON,
        metro_message=run_in(NOON - timedelta(minutes=140)),
        metro_ready_at=Obs(value=entered, at=entered),
        artifact_collect=seen(at=NOON),
    )
    cfg = mode(METRO_ALONE, started=NOON - timedelta(hours=1), ends=NOON + timedelta(days=9))
    last = {"metro": NOON - timedelta(hours=20)}
    decision = decide(s, cfg, NOON, last_done=last)
    assert not isinstance(decision, Act) or decision.scenario != "metro"
    assert verdicts(decision)["metro"] == "artifact_run"
    kick = Wakeup(msk(21, 45) + TIMER_MARGIN, "metro_kick")
    assert kick in outlook(s, cfg, NOON, last_done=last).wakeups
