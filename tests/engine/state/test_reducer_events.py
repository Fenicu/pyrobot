from dataclasses import replace
from datetime import timedelta

from app.engine.parsing import default_parser
from app.engine.parsing.common import Rewards
from app.engine.parsing.crew import CrewScreen
from app.engine.parsing.screens import LotterySkillsExpired
from app.engine.settings import ChatsSection
from app.engine.state.model import load_state, stale_fields
from app.engine.state.reducer import StateReducer
from tests.engine.parsing.test_tangerine import GIFTS
from tests.engine.state.helpers import PARSER, at, feed, fixture_at, value
from tests.fixtures import game_versions

INVITES = -1009999
PARSER_WITH_INVITES = default_parser(ChatsSection(bulls_invite_chat_id=INVITES))


def _profiled(reducer: StateReducer) -> dict:
    return feed(reducer, {}, "profile", 3624478, 0)


def test_team_and_factory() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "crew", 3624389, 1)
    assert value(state, "factory_wins") == 247
    state = feed(reducer, state, "crew", 3624391, 2)
    assert value(state, "factory_signed") is False
    state = feed(reducer, state, "crew", 3624393, 3)
    assert value(state, "factory_signed") is True
    state = feed(reducer, state, "crew", 3621811, 4)
    assert value(state, "factory_skip") is True
    state = feed(reducer, state, "swinfo", 3817108, 5)
    assert value(state, "factory_call_at") == "2026-09-26T09:05:00Z"


def test_factory_win_of_own_team_only() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "swinfo", 3816957, 1)
    assert value(state, "factory_won_at") is None
    state = feed(reducer, state, "crew", 3624389, 2)
    assert value(state, "team_tag") == "SU"
    state = feed(reducer, state, "swinfo", 3817112, 3)
    assert value(state, "factory_won_at") is None
    state = feed(reducer, state, "swinfo", 3816957, 4)
    assert value(state, "factory_won_at") == "2026-09-26T09:04:00Z"


def test_personal_glory_from_crew_screen_is_a_metric() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "crew", 3624389, 1)
    assert value(state, "glory") == 45090
    later = feed(reducer, state, "crew", 3626163, 2)
    assert value(later, "glory") == 45180
    assert reducer.metrics(state, later) == {"glory": 45180.0}


def test_factory_report_day_only_grows() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "crew", 3620025, 1)
    assert value(state, "factory_report_day") == "2026-09-09"
    state = feed(reducer, state, "crew", 3625108, 2)
    assert value(state, "factory_report_day") == "2026-09-25"
    # Отчёт о более ранней битве, увиденный позже, известный день не откатывает.
    assert feed(reducer, state, "crew", 3620025, 3) == state
    assert value(state, "money") is None


def test_factory_report_day_not_from_future() -> None:
    # День битвы позже сегодняшнего (по Москве, на момент сообщения) не принимается: он «только
    # растёт» и залип бы, отключив запрос сегодняшнего отчёта до той даты.
    import re

    reducer = StateReducer()
    state = feed(reducer, {}, "crew", 3625108, 1)
    report = fixture_at("crew", 3625108, 2)

    def dated(day: str) -> object:
        text = re.sub(r"фабрику \d+\.\d+\.\d+:", f"фабрику {day}:", report.text or "")
        return replace(report, text=text)

    future = dated("27.09.26")
    state = reducer.apply(state, future, PARSER.parse(future))
    assert value(state, "factory_report_day") == "2026-09-25"
    today = dated("26.09.26")
    state = reducer.apply(state, today, PARSER.parse(today))
    assert value(state, "factory_report_day") == "2026-09-26"


def test_closed_factory_screen_keeps_signup() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "crew", 3624393, 1)
    state = feed(reducer, state, "crew", 3586815, 2)
    assert value(state, "factory_signed") is True


def test_crew_screen_signup_open_sets_factory_call_at() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "crew", 3624389, 1)
    assert value(state, "factory_call_at") == "2026-09-26T09:01:00Z"


def test_crew_screen_signup_closed_keeps_factory_call_at() -> None:
    reducer = StateReducer()
    msg = fixture_at("crew", 3624389, 1)
    event = CrewScreen(tag="SU", factory_wins=247, signup_open=False)
    state = reducer.apply({}, msg, [event])
    assert value(state, "factory_call_at") is None


def test_bulls_fight_busy_rewards_and_night_flag() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "bulls", 3624430, 1)
    assert value(state, "busy") == {"activity": "bulls", "until": "2026-09-26T09:06:00Z"}
    assert state["busy"]["src"] == "derived"
    state = feed(reducer, state, "bulls", 3624431, 5)
    assert value(state, "busy") is None
    assert value(state, "money") == 867 + 150
    assert value(state, "bulls_won_at") == "2026-09-26T09:05:00Z"


def test_bulls_already_won_marks_night() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "bulls", 3610979, 3)
    assert value(state, "bulls_won_at") == "2026-09-26T09:03:00Z"
    state = feed(reducer, state, "bulls", 3526549, 4)
    assert value(state, "bulls_won_at") == "2026-09-26T09:03:00Z"


def test_bulls_result_keeps_unrelated_busy() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "activities", 3517898, 1)
    assert value(state, "busy")["activity"] == "job"
    state = feed(reducer, state, "bulls", 3624431, 5)
    assert value(state, "busy")["activity"] == "job"
    assert value(state, "money") == 867 + 150


def test_stock_screen_trade_and_dividends() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "stocks", 3624065, 1)
    assert value(state, "stock_quotes")["umbrl"] == 100
    assert value(state, "stock_holdings")["hooli"] == 1838
    assert value(state, "stock_limits") == {
        "min_buy": 11,
        "max_sell": 80,
        "reserve": 100,
        "open_hour": 8,
        "close_hour": 22,
    }
    assert value(state, "money") == 2364
    state = feed(reducer, state, "stocks", 3564237, 2)
    assert value(state, "money") == 110
    assert value(state, "stock_holdings")["umbrl"] == 1232
    assert value(state, "stock_holdings")["hooli"] == 1838
    state = feed(reducer, state, "stocks", 3621194, 3)
    assert value(state, "money") == 110 + 1065


def test_stock_bought_without_known_portfolio() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "stocks", 3564237, 1)
    assert value(state, "stock_holdings") is None
    assert value(state, "money") == 110


def test_stock_sold_updates_money_and_known_portfolio() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "stocks", 3624065, 1)
    state = feed(reducer, state, "stocks", 3625251, 2)
    assert value(state, "money") == 699
    assert value(state, "stock_holdings")["piper"] == 3762
    assert value(state, "stock_holdings")["hooli"] == 1838


def test_buy_screen_keeps_main_limits() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "stocks", 3624065, 1)
    state = feed(reducer, state, "stocks", 3624067, 2)
    assert value(state, "stock_limits")["max_sell"] == 80


def test_battle_summary_updates_quotes() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "stocks", 3592131, 1)
    assert value(state, "stock_quotes")["stark"] == 36
    state = feed(reducer, state, "swinfo", 3817131, 2)
    assert value(state, "stock_quotes")["stark"] == 31


def test_smoothie() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "smoothie", 3581573, 1)
    assert value(state, "smoothie_bonus") is None
    assert value(state, "smoothie_ingredients")["tomato"] == 4
    state = feed(reducer, state, "smoothie", 3581575, 2)
    assert value(state, "smoothie_bonus").startswith("🍴На еде")
    # Рецепт "🍇🥕🥕🍋🍅" — 1 виноград, 2 моркови, 1 лимон, 1 томат, яблоко не тронуто.
    ingredients = value(state, "smoothie_ingredients")
    assert (ingredients["grape"], ingredients["carrot"], ingredients["lemon"]) == (3, 2, 3)
    assert (ingredients["tomato"], ingredients["apple"]) == (3, 4)
    state = feed(reducer, state, "smoothie", 2344, 3)
    assert value(state, "smoothie_recipe")["recipe"] == "🍇🥕🥕🍋🍅"


def test_smoothie_cooked_ingredients_decrease_once() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "smoothie", 3581573, 1)
    state = feed(reducer, state, "smoothie", 3581575, 2)
    once = value(state, "smoothie_ingredients")
    again = feed(reducer, state, "smoothie", 3581575, 3, created=2)
    assert value(again, "smoothie_ingredients") == once


def test_tangerine_refusals() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "tangerine", 3616906, 1)
    assert value(state, "tangerine_ready_at") == "2026-09-27T04:55:00Z"
    state = feed(reducer, state, "tangerine", 3599304, 2)
    assert value(state, "tangerine_not_player") == "𝐿𝑜𝓁𝒾𝒸𝒽𝒶𝓃𝓂𝒶𝓎"


def test_received_tangerines_add_once() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "profile", 3624478, 0)
    gift = replace(fixture_at("tangerine", 3616906, 1), text=GIFTS[0][0])
    state, effects = reducer.reduce(state, gift, PARSER.parse(gift))
    assert (value(state, "tangerines"), state["tangerines"]["src"], effects) == (4, "derived", ())
    edit = replace(gift, date=at(2), revision=1)
    assert value(reducer.apply(state, edit, PARSER.parse(edit)), "tangerines") == 4


def test_battle_menu_sets_next_battle() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "screens", 3613862, 1)
    assert value(state, "battle_at") == "2026-09-26T13:14:00Z"


def test_event_flags_do_not_expire_by_age() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "bulls", 3610979, 1)
    state = feed(reducer, state, "tangerine", 3616906, 1)
    state = feed(reducer, state, "crew", 3624393, 1)
    stale = stale_fields(load_state(state), at(24 * 60), timedelta(minutes=15))
    assert not {"bulls_won_at", "tangerine_ready_at", "factory_signed"} & set(stale)


def test_results_change_resources_once() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "screens", 3540652, 1)
    assert value(state, "money") == 867 + 516
    again = feed(reducer, state, "screens", 3540652, 2, created=1)
    assert value(again, "money") == 867 + 516
    state = feed(reducer, state, "screens", 3606840, 3)
    assert value(state, "money") == 130


def test_lottery_win_and_expired_skills() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "screens", 3533470, 1)
    assert value(state, "knowledge") == 21942 + 100
    assert value(state, "skills")["theory"] == 460 + 1
    state = feed(reducer, state, "screens", 3520541, 2)
    assert value(state, "motivation") == 72 + 10
    state = feed(reducer, state, "screens", 3545091, 3)
    assert value(state, "skills")["theory"] == 460
    assert value(state, "exp") == 17_496_049 + 142


def test_lottery_skills_expired_counts_duplicate_skills() -> None:
    reducer = StateReducer()
    state = feed(reducer, _profiled(reducer), "screens", 3545091, 1)
    assert value(state, "skills")["practice"] == 461
    # Событие собрано напрямую: в корпусе нет фикстуры с потерей одного навыка дважды.
    msg = fixture_at("screens", 3606840, 2)
    event = LotterySkillsExpired(skills=("practice", "practice"), rewards=Rewards())
    state = reducer.apply(state, msg, [event])
    assert value(state, "skills")["practice"] == 461 - 2


def test_instant_finish_frees_character() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "activities", 3517898, 1)
    assert value(state, "busy") is not None
    state = feed(reducer, state, "screens", 3603799, 2)
    assert value(state, "busy") is None


def test_gadgets_screens_update_money() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "screens", 3568823, 1)
    assert value(state, "money") == 445
    state = feed(reducer, state, "screens", 3525610, 2)
    assert value(state, "money") == 3250


def test_bulls_invite_remembered() -> None:
    reducer = StateReducer()
    invite = fixture_at("bulls_invite", 3681068, 1)
    state = reducer.apply({}, invite, PARSER_WITH_INVITES.parse(replace(invite, chat_id=INVITES)))
    assert value(state, "bulls_invite") == "join_fight_AaBH89kYd2J"


def test_target_remembers_what_and_for_which_battle() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "battle", 3624402, 1)
    assert value(state, "battle_target") == "📯Pied Piper"
    assert value(state, "battle_target_set") == {
        "target": "📯Pied Piper",
        "battle_at": value(state, "battle_at"),
    }
    assert value(state, "battle_at") == "2026-09-26T11:45:00Z"


def test_cooking_screen_does_not_count_drops_twice() -> None:
    reducer = StateReducer()
    state = feed(reducer, {}, "smoothie", 3581573, 1)
    # Экран варки показывает остатки уже без вброшенного, итог отнимает рецепт от экрана.
    for version, msg in enumerate(game_versions("smoothie_cooking", 3625241)):
        edit = replace(msg, date=at(2 + version), created_at=at(2))
        state = reducer.apply(state, edit, PARSER.parse(edit))
    ingredients = value(state, "smoothie_ingredients")
    assert ingredients == {"lemon": 3, "grape": 3, "apple": 4, "carrot": 2, "tomato": 3}
    assert value(state, "smoothie_bonus") is None
