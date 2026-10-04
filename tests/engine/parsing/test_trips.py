import json
from datetime import date, timedelta

import pytest

from app.engine.parsing import default_parser
from app.engine.parsing.activities import ActivityFinished
from app.engine.parsing.common import Rewards
from app.engine.parsing.refusals import Busy
from app.engine.parsing.trips import (
    TRIP,
    TRIP_SPAN,
    VEHICLES,
    RewardsOnly,
    TripRefused,
    TripsScreen,
    TripStarted,
    TripVehicle,
    recognize_rewards_only,
    recognize_trips,
    season_end,
    vehicle_key,
)
from app.engine.settings import ChatsSection
from tests.engine import trip_texts as t
from tests.engine.artifact_texts import game_text

PARSER = default_parser(ChatsSection())
H, M = 3600, 60


def parse(text: str) -> list[object]:
    return list(PARSER.parse(game_text(text)))


def screen_of(text: str) -> TripsScreen:
    [screen] = recognize_trips(game_text(text))
    assert isinstance(screen, TripsScreen)
    return screen


def test_catalogue_keys_names_buttons_cooldowns() -> None:
    table = {k: (v.screen, v.button, v.cooldown) for k, v in VEHICLES.items()}
    assert table == {
        "car": ("🚕Ааавтомобиль", "🚕Тачка", timedelta(hours=20)),
        "tram": ("🚃Трамвай", "🚃Трамвай", timedelta(hours=18)),
        "sled": ("🛷Санки", "🛷Санки", timedelta(hours=18)),
        "bike": ("🚲Велосипед", "🚲Велик", timedelta(hours=23)),
        "scooter": ("🛴Самокат", "🛴Самокат", timedelta(hours=5)),
        "tractor": ("🚜Трактор", "🚜Трактор", timedelta(hours=19)),
    }
    assert TRIP_SPAN == timedelta(minutes=10)
    assert TRIP == "trip"


def test_unknown_vehicle_keyed_by_its_screen_name() -> None:
    assert vehicle_key("🚕Ааавтомобиль") == "car"
    assert vehicle_key("🚁Вертолёт") == "🚁Вертолёт"


def test_current_screen_prices_all_ready() -> None:
    assert parse(t.SCREEN) == [
        TripsScreen(
            vehicles=(
                TripVehicle(key="bike", name="🚲Велосипед", available=True, raw=5, money=0),
                TripVehicle(key="car", name="🚕Ааавтомобиль", available=True, raw=8, money=20),
                TripVehicle(key="tram", name="🚃Трамвай", available=True, raw=10, money=30),
            )
        )
    ]


@pytest.mark.parametrize(
    ("text", "left"),
    [
        (
            t.SCREEN_ALL_COOLDOWN,
            {"bike": 22 * H + 49 * M, "car": 19 * H + 39 * M, "tram": 17 * H + 20 * M},
        ),
        (t.SCREEN_HOURS_ONLY, {"car": 19 * H + 49 * M, "tram": 17 * H + 11 * M, "sled": 17 * H}),
        (t.SCREEN_MINUTES_ONLY, {"scooter": 39 * M}),
        (t.SCREEN_MINUTES_SECONDS, {"scooter": 7 * M + 15}),
        (
            t.SCREEN_SCOOTER_COOLDOWN,
            {"car": 18 * H + 24 * M, "tram": 16 * H + 7 * M, "scooter": 2 * H + 37 * M},
        ),
    ],
)
def test_screen_left_in_every_format(text: str, left: dict[str, int]) -> None:
    screen = screen_of(text)
    assert {v.key: v.left_s for v in screen.vehicles if v.left_s is not None} == left


def test_screen_seasonal_expiry_is_on_the_last_vehicle() -> None:
    screen = screen_of(t.SCREEN_TRACTOR_COOLDOWN)
    tractor = screen.vehicles[-1]
    assert (tractor.key, tractor.raw, tractor.money) == ("tractor", 10, 30)
    assert (tractor.left_s, tractor.expires) == (17 * H + 56 * M, (1, 31))
    assert all(v.expires is None for v in screen.vehicles[:-1])
    sled = screen_of(t.SCREEN_SLED_COOLDOWN).vehicles[-1]
    assert (sled.key, sled.raw, sled.money) == ("sled", 9, 25)
    assert (sled.left_s, sled.expires) == (6 * H + 21 * M, (5, 9))
    assert screen_of(t.SCREEN_SLED_APRIL).vehicles[-1].expires == (4, 30)


@pytest.mark.parametrize(
    ("text", "key", "name"),
    [
        (t.SCREEN_TRAM_RAIL, "tram", "🚃Трамвай"),
        (t.SCREEN_SLED_SHARPENING, "sled", "🛷Санки"),
    ],
)
def test_screen_placeholder_is_unavailable(text: str, key: str, name: str) -> None:
    screen = screen_of(text)
    assert screen.vehicles[-1] == TripVehicle(key=key, name=name, available=False)
    assert all(v.available for v in screen.vehicles[:-1])


def test_screen_unknown_vehicle_kept_by_name() -> None:
    text = t.SCREEN + "\n\n🚁Вертолёт - 50🔩, 100 💵, 10 ⏰\nЧерез 3ч."
    assert screen_of(text).vehicles[-1] == TripVehicle(
        key="🚁Вертолёт", name="🚁Вертолёт", available=True, raw=50, money=100, left_s=3 * H
    )


@pytest.mark.parametrize(
    "line",
    [
        "🚲Велосипед - 5🔩, 10 ⏰, 3 🍊",
        "🚲Велосипед - $5, 10 ⏰",
        "🚲Велосипед - 5 сырья",
        "🚲Велосипед - бесплатно, 10 ⏰",
        "🚲Велосипед\n5🔩, 10 ⏰",
    ],
)
def test_screen_with_unknown_price_format_is_unrecognized(line: str) -> None:
    # Незнакомый формат цены — не заглушка: иначе вид молча стал бы недоступным навсегда.
    text = t.SCREEN.replace("🚲Велосипед - 5🔩, 10 ⏰", line)
    assert recognize_trips(game_text(text)) == []
    assert [type(e).__name__ for e in parse(text)] == ["Unrecognized"]


def test_screen_with_unknown_line_is_not_a_partial_snapshot() -> None:
    assert recognize_trips(game_text(t.SCREEN + "\nчто-то новое")) == []


@pytest.mark.parametrize(
    ("text", "vehicle", "raw", "money"),
    [
        (t.START_BIKE, "bike", 5, 0),
        (t.START_BIKE_2024, "bike", 5, 0),
        (t.START_CAR, "car", 8, 20),
        (t.START_TRAM, "tram", 10, 30),
        (t.START_TRAM_2021, "tram", 10, 30),
        (t.START_SLED, "sled", 9, 25),
        (t.START_SLED_2021, "sled", 9, 25),
    ],
)
def test_start_of_every_vehicle(text: str, vehicle: str, raw: int, money: int) -> None:
    assert parse(text) == [TripStarted(vehicle=vehicle, raw=raw, money=money, duration_s=600)]


def test_start_of_unknown_vehicle_by_generic_anchor() -> None:
    text = "Завёл 🚁Вертолёт. Перед поездкой залил 50🔩 и 100 💵. Вернёшься через 10 минут."
    assert parse(text) == [TripStarted(vehicle=None, raw=50, money=100, duration_s=600)]


@pytest.mark.parametrize(
    ("text", "vehicle", "left_s"),
    [
        (t.REFUSAL_BIKE, "bike", 22 * H + 47 * M),
        (t.REFUSAL_BIKE_MINUTES, "bike", 13 * M),
        (t.REFUSAL_CAR, "car", 19 * H + 25 * M),
        (t.REFUSAL_TRAM, "tram", 17 * H + 8 * M),
        (t.REFUSAL_SLED, "sled", 6 * H + 21 * M),
    ],
)
def test_cooldown_refusal_of_every_vehicle(text: str, vehicle: str, left_s: int) -> None:
    assert parse(text) == [TripRefused(vehicle=vehicle, left_s=left_s)]


def test_busy_refusal_is_the_generic_one() -> None:
    assert parse(t.BUSY_DURING_TRIP) == [Busy(left_s=9 * M + 55)]


@pytest.mark.parametrize(
    ("text", "rewards"),
    [
        (t.RESULT_BIKE, Rewards(knowledge=16)),
        (t.RESULT_CAR_RED, Rewards(upgrades_red=1)),
        (t.RESULT_CAR_DETAILS, Rewards(details=12)),
        (t.RESULT_CAR_BONUS, Rewards()),
        (t.RESULT_TRAM_EXP, Rewards(exp=244)),
        (t.RESULT_TRAM_YODA, Rewards()),
        (t.RESULT_TRAM_PRIZEBOX, Rewards(prizebox=True)),
        (t.RESULT_TRAM_MONEY, Rewards(money=215)),
        (t.RESULT_SLED_DETAILS_NO_VS16, Rewards(details=17)),
        (t.RESULT_SLED_BLUE, Rewards(upgrades_blue=2)),
        (t.RESULT_SLED_RAW, Rewards(raw=14)),
    ],
)
def test_result_is_rewards_only(text: str, rewards: Rewards) -> None:
    assert parse(text) == [RewardsOnly(rewards=rewards)]


def test_result_without_reward_is_rewards_only_with_nothing() -> None:
    assert parse(t.RESULT_NOTHING) == [RewardsOnly(rewards=Rewards())]
    # Только целиком: продолжение после фразы — уже не итог поездки.
    assert recognize_rewards_only(game_text(t.RESULT_NOTHING + "\n\n/job")) == []


def test_rewards_only_is_a_fallback_after_every_family() -> None:
    # Итог дела с той же формой, но со строкой продолжения — итог дела, не «только награда».
    deed = (
        "Сегодня каждый переработчик обязан думать о завтрашнем дне... Только каким оно будет, "
        "это самое дно?\n\nТы получил:\n💡Опыт: +163\n🔩Сырьё: +5\n\n"
        "Перерабатывать ⚙️ → 🔩 ещё - /dconv"
    )
    [event] = parse(deed)
    assert isinstance(event, ActivityFinished)
    # Распознаватель формы сам по себе на это сообщение не отзывается: блок не последний.
    assert recognize_rewards_only(game_text(deed)) == []
    woke = (
        "Ты отлично выспался на свежем воздухе. Неплохо восстановил силы.\n\n"
        "Ты получил:\n💡Опыт: +166\n🔋Выносливость: 100%"
    )
    assert [type(e).__name__ for e in parse(woke)] == ["WokeUp"]


def test_events_are_json_safe() -> None:
    for text in (t.SCREEN_TRACTOR_COOLDOWN, t.START_CAR, t.REFUSAL_SLED, t.RESULT_BIKE):
        for event in PARSER.parse(game_text(text)):
            json.dumps(event.to_json(), ensure_ascii=False)


@pytest.mark.parametrize(
    ("month_day", "seen", "expected"),
    [
        ((1, 31), date(2019, 12, 5), date(2020, 1, 31)),
        ((5, 9), date(2024, 2, 16), date(2024, 5, 9)),
        ((4, 30), date(2021, 4, 30), date(2021, 4, 30)),
        ((2, 29), date(2023, 2, 1), date(2024, 2, 29)),
        # Вид на экране не кончился: срок — первая дата не раньше дня экрана.
        ((5, 9), date(2026, 10, 5), date(2027, 5, 9)),
        ((5, 9), date(2026, 4, 20), date(2026, 5, 9)),
        ((5, 9), date(2026, 5, 9), date(2026, 5, 9)),
        # Запас в сутки на часовой пояс: экран на следующий день после срока.
        ((5, 9), date(2022, 5, 10), date(2022, 5, 9)),
        ((1, 31), date(2020, 2, 1), date(2020, 1, 31)),
        ((5, 9), date(2022, 5, 11), date(2023, 5, 9)),
    ],
)
def test_season_end_first_date_not_before_the_screen(
    month_day: tuple[int, int], seen: date, expected: date
) -> None:
    assert season_end(month_day, seen) == expected
