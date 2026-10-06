from dataclasses import replace

import pytest

from app.engine.events import Event
from app.engine.parsing.activities import (
    _STARTS,
    RECOGNIZERS,
    ActivityCancelled,
    ActivityFinished,
    ActivityStarted,
    BonusRewards,
    DeedsMenu,
    MotivationFull,
    Price,
    PricesScreen,
    WorkshopScreen,
)
from tests.fixtures import game_msg


def _events(msg_id: int) -> list[Event]:
    msg = game_msg("activities", msg_id)
    return [e for recognize in RECOGNIZERS for e in recognize(msg)]


@pytest.mark.parametrize(
    ("msg_id", "expected"),
    [
        (3517276, ActivityStarted(activity="harvest", duration_s=300, money=30)),
        (3517898, ActivityStarted(activity="job", duration_s=145)),
        (3623881, ActivityStarted(activity="job", duration_s=120)),
        (3603614, ActivityStarted(activity="learn", duration_s=210)),
        (3516647, ActivityStarted(activity="eat", duration_s=300, money=5)),
        (3624728, ActivityStarted(activity="dconv", duration_s=360, money=5, details=10)),
        # Прогулка со 🏌️Клюшкой (живой замер), ⛷лыжами и 🐕.
        (3625686, ActivityStarted(activity="walk", duration_s=250)),
        (3385717, ActivityStarted(activity="walk", duration_s=280)),
        (3213190, ActivityStarted(activity="walk", duration_s=247)),
        (3437620, ActivityStarted(activity="confa", duration_s=240, money=7)),
        (3438033, ActivityStarted(activity="confa", duration_s=240, money=7)),
    ],
)
def test_starts(msg_id: int, expected: ActivityStarted) -> None:
    assert _events(msg_id) == [expected]


# Персонаж без пета и снаряжения (прод, 11 уровень).
_WALK_BARE = (
    '"Немного пройдусь", - сказал ты всем в офисе, но никто не услышал. Всем пофиг. '
    "Вернёшься через 5 минут.\n\nОтменить: /decline"
)
_JOB_BADMINTON = (
    "Взял любимый 🏸Бадминтон - размяться с коллегами на работе. "
    "Закончишь через 4 мин. 55 сек.\n\nОтменить: /decline"
)


# Живые 04–05.10.2026 (прод): старты с 🐕 (добыча и переработка), короткая работа и учёба.
HARVEST_DOG = (
    "Ты начал разыскивать комплектующие на забытых развалах и бордах в интернете. Взял любимого "
    "🐕 - он и быстрей полезное унюхает, так ещё и сам что-нибудь принесёт. Затраты - 30 💵. "
    "Закончишь через 5 мин.\n\nОтменить: /decline\nЗавершить: /finish"
)
DCONV_DOG = (
    "Ты перерабатываешь детали в сырьё, верный 🐕 на подхвате. Заплатил 5\xa0💵 за доступ к "
    "станку. Выложил 10\xa0⚙️деталей. Закончишь через 3 мин.\n\nОтменить: /decline\n"
    "Завершить: /finish"
)
JOB_SHORT = (
    "Ты решаешь немного поработать. Минут 5, не больше.\n\nОтменить: /decline\nЗавершить: /finish"
)
LEARN_LIGHT = (
    "Ученье - свет. А также вода и центральное отопление... Через 7 минут закончишь обучение."
    "\n\nОтменить: /decline"
)


DECLINED = "Действие отменено."


def _text_events(text: str) -> list[Event]:
    msg = replace(game_msg("activities", 3625686), text=text)
    return [e for recognize in RECOGNIZERS for e in recognize(msg)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (_WALK_BARE, ActivityStarted(activity="walk", duration_s=300)),
        (_JOB_BADMINTON, ActivityStarted(activity="job", duration_s=295)),
    ],
    ids=["walk", "job"],
)
def test_starts_without_pet(text: str, expected: ActivityStarted) -> None:
    assert _text_events(text) == [expected]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (HARVEST_DOG, ActivityStarted(activity="harvest", duration_s=300, money=30)),
        (DCONV_DOG, ActivityStarted(activity="dconv", duration_s=180, money=5, details=10)),
        (JOB_SHORT, ActivityStarted(activity="job", duration_s=300)),
        (LEARN_LIGHT, ActivityStarted(activity="learn", duration_s=420)),
    ],
    ids=["harvest_dog", "dconv_dog", "job_short", "learn_light"],
)
def test_starts_live_variants(text: str, expected: ActivityStarted) -> None:
    assert _text_events(text) == [expected]


@pytest.mark.parametrize(
    ("text", "activity"),
    [
        (_WALK_BARE, "walk"),
        (_JOB_BADMINTON, "job"),
        (HARVEST_DOG, "harvest"),
        (DCONV_DOG, "dconv"),
        (JOB_SHORT, "job"),
        (LEARN_LIGHT, "learn"),
        *(
            (game_msg("activities", msg_id).text, activity)
            for msg_id, activity in [
                (3517276, "harvest"),
                (3517898, "job"),
                (3603614, "learn"),
                (3624728, "dconv"),
                (3625686, "walk"),
                (3213190, "walk"),
            ]
        ),
    ],
    ids=[
        "walk_bare",
        "job_badminton",
        "harvest_dog",
        "dconv_dog",
        "job_short",
        "learn_light",
        "harvest",
        "job",
        "learn",
        "dconv",
        "walk_club",
        "walk_dog",
    ],
)
def test_start_patterns_do_not_overlap(text: str, activity: str) -> None:
    assert [kind for kind, pattern in _STARTS if pattern.match(text)] == [activity]


def _finished(msg_id: int) -> ActivityFinished:
    events = _events(msg_id)
    assert len(events) == 1 and isinstance(events[0], ActivityFinished)
    return events[0]


def test_harvest_results() -> None:
    assert (_finished(3517279).activity, _finished(3517279).rewards.exp) == ("harvest", 158)
    assert _finished(3610659).rewards.exp == 348
    assert _finished(3610665).rewards.exp == 233
    assert (_finished(3517344).failed, _finished(3518414).failed) == (True, True)
    assert _finished(3517279).failed is False


def test_other_results() -> None:
    job = _finished(3517901)
    assert (job.activity, job.rewards.money, job.rewards.details) == ("job", 28, 4)
    learn = _finished(3603617)
    assert (learn.activity, learn.rewards.knowledge) == ("learn", 9)
    assert [_finished(i).rewards.stamina for i in (3516648, 3517759, 3520195)] == [100, 0, 200]
    dconv = _finished(3624873)
    assert (dconv.activity, dconv.rewards.exp, dconv.rewards.raw) == ("dconv", 253, 5)


def test_walk_results() -> None:
    walk = _finished(3625689)
    assert (walk.activity, walk.failed) == ("walk", False)
    r = walk.rewards
    assert (r.exp, r.money, r.raw, r.team_task) == (253, 4, 1, (18, 120, "🔩"))
    plain = _finished(3436320)
    assert (plain.activity, plain.rewards.exp, plain.rewards.money) == ("walk", 175, 3)
    # Итог без наград — тоже итог прогулки.
    empty = _finished(3428028)
    assert (empty.activity, empty.rewards.exp) == ("walk", 0)


def test_confa_results() -> None:
    owl = _finished(3437625)
    assert (owl.activity, owl.rewards.exp, owl.rewards.knowledge) == ("confa", 394, 99)
    team = _finished(3438035)
    assert (team.rewards.knowledge, team.rewards.team_task) == (88, (437, 600, "📚"))
    empty = _finished(3437621)
    assert (empty.activity, empty.rewards.knowledge) == ("confa", 0)


def test_logistic_refund() -> None:
    finished = _finished(3623797)
    assert (finished.activity, finished.motivation_refund, finished.rewards.exp) == ("job", 1, 0)


def test_cancel_variants() -> None:
    assert _events(3517963) == [ActivityCancelled(result="ok", motivation=1)]
    assert _events(3522301) == [ActivityCancelled(result="ok", money=5)]
    assert _events(3618769) == [ActivityCancelled(result="ok", money=30, motivation=1)]
    assert _events(3517930) == [ActivityCancelled(result="too_late")]
    assert _events(3529038) == [ActivityCancelled(result="nothing")]
    # Живой 05.10.2026: ответ на /decline без возврата.
    assert _text_events(DECLINED) == [ActivityCancelled(result="ok")]


def test_motivation_full_and_magnet() -> None:
    assert _events(3517795) == [MotivationFull()]
    magnet = _events(3524596)
    assert len(magnet) == 1 and isinstance(magnet[0], BonusRewards)
    assert (magnet[0].source, magnet[0].rewards.exp) == ("magnet", 247)


def test_deeds_menu_compact_and_full() -> None:
    deeds = {
        "job": Price(motivation=1, minutes=5),
        "walk": Price(motivation=1, minutes=5),
        "rob": Price(motivation=1, minutes=8),
    }
    assert _events(3610643) == [
        PricesScreen(screen="deeds", prices=deeds),
        DeedsMenu(stamina=100, sleep_in_s=3360, sleeping=None, sleeping_left_s=None),
    ]
    assert _events(3548692)[1] == DeedsMenu(
        stamina=100, sleep_in_s=None, sleeping="hotel", sleeping_left_s=25140
    )
    assert _events(3516791) == [
        PricesScreen(screen="deeds", prices=deeds),
        DeedsMenu(stamina=100, sleep_in_s=118800, sleeping=None, sleeping_left_s=None),
    ]


def test_startup_workshop_profession_prices() -> None:
    assert _events(3624645) == [
        PricesScreen(
            screen="startup",
            prices={
                "learn": Price(motivation=2, minutes=7),
                "confa": Price(motivation=3, money=7, minutes=8),
            },
        )
    ]
    assert _events(3624750) == [
        PricesScreen(
            screen="workshop",
            prices={
                "dconv": Price(motivation=1, money=5, minutes=6, details=10),
                "white_to_blue": Price(motivation=1, money=5, minutes=6, white=3),
                "blue_to_red": Price(motivation=2, money=10, minutes=6, blue=3),
            },
        ),
        WorkshopScreen(
            money=862,
            raw=21310,
            details=136661,
            upgrades_white=10243,
            upgrades_blue=4797,
            upgrades_red=2525,
        ),
    ]
    assert _events(3517254) == [
        PricesScreen(
            screen="profession", prices={"harvest": Price(motivation=1, money=30, minutes=10)}
        )
    ]


@pytest.mark.parametrize(
    ("msg_id", "drop"),
    [
        (3610643, "🔫Грабить — 1🔥, 8 ⏰"),
        (3610643, "🔋Выносливость: 100%"),
        (3624645, "Требования: 3🔥, 7 💵, 8 ⏰"),
        (3624750, "⚪️ простые:"),
        (3624750, "🔵 → 🔴 - создать уникальные улучшения из редких."),
    ],
)
def test_partial_screen_gives_nothing(msg_id: int, drop: str) -> None:
    msg = game_msg("activities", msg_id)
    assert msg.text is not None and drop in msg.text
    broken = replace(msg, text=msg.text.replace(drop, "…"))
    assert [e for recognize in RECOGNIZERS for e in recognize(broken)] == []


@pytest.mark.parametrize(
    ("msg_id", "exp", "items", "small", "medium"),
    [
        (3517279, 158, {"Пуговица": 1, "Нитки": 1}, 0, 0),
        (3610659, 348, {"Кусок ткани": 1, "Пуговица": 1, "Нитки": 1}, 0, 0),
        # Сет Логистик: «🗳М. контейнер: +1 (2)» — малый контейнер к имеющимся.
        (3610665, 233, {"Шнурок": 1, "Льняная ткань": 1}, 1, 0),
        (3609456, 184, {"Нитки": 1, "Шнурок": 2, "Пуговица": 1}, 0, 1),
        # 🎓Диплом — отдельный блок награды с отступом.
        (3624009, 250, {"Кусок ткани": 1, "Резинка": 2, "Льняная ткань": 1}, 0, 0),
        (3517344, 0, {}, 0, 0),
    ],
)
def test_harvest_craft_items_and_containers(
    msg_id: int, exp: int, items: dict[str, int], small: int, medium: int
) -> None:
    r = _finished(msg_id).rewards
    assert (r.exp, r.items, r.containers_small, r.containers_medium) == (
        exp,
        items,
        small,
        medium,
    )


def test_job_item_without_pet_food() -> None:
    # «🧀 для 🐀Аля: +1» — еда пета, не предмет крафта.
    r = _finished(3623749).rewards
    assert (r.exp, r.money, r.raw, r.items) == (136, 27, 1, {"Флюс": 1})


def test_walk_pet_food_is_not_an_item() -> None:
    assert _finished(3625689).rewards.items == {}


def test_magnet_items() -> None:
    [magnet] = _events(3524596)
    assert isinstance(magnet, BonusRewards)
    assert (magnet.rewards.exp, magnet.rewards.items) == (247, {"Пуговица": 1, "Нитки": 1})
