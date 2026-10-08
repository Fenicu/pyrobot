from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.engine.parsing.common import COMPANIES
from app.engine.parsing.trips import VEHICLES


class CommandClass(StrEnum):
    NAV = "nav"
    ACTION = "action"
    RISKY = "risky"
    FORBIDDEN = "forbidden"
    DONATE = "donate"
    # Пересылка сообщения игры в чат команды: не игровая команда, у шлюза своя политика.
    FORWARD = "forward"


@dataclass(frozen=True, slots=True)
class Rule:
    pattern: re.Pattern[str]
    cls: CommandClass


def _exact(cls: CommandClass, *texts: str) -> list[Rule]:
    return [Rule(re.compile(re.escape(t) + r"\Z"), cls) for t in texts]


def _re(cls: CommandClass, *patterns: str) -> list[Rule]:
    return [Rule(re.compile(p), cls) for p in patterns]


_F, _D, _R, _N, _A = (
    CommandClass.FORBIDDEN,
    CommandClass.DONATE,
    CommandClass.RISKY,
    CommandClass.NAV,
    CommandClass.ACTION,
)
_COMPANIES = "(?:" + "|".join(COMPANIES.values()) + ")"
# Акции: своя компания — только вручную и с подтверждением (ими распоряжается CEO компании),
# чужие — обычное действие. Своя неизвестна — любая может ей оказаться.
_STOCK_TEXT = re.compile(rf"/(?:buys|sells)_(?P<company>{_COMPANIES})_[0-9]+\Z")
_STOCK_CALLBACK = re.compile(rf"buys_(?P<company>{_COMPANIES})\Z")
STOCK_SELL = re.compile(rf"/sells_{_COMPANIES}_[0-9]+\Z")

_UP_SLOTS = "right|left|legs|head|chest|torso|ring|book|pbank|pants"
# Гаджеты: магазин (тиры 1–14 шести слотов), надевание и снятие по коду, экран заточки слота.
# Покупку и надевание без подтверждения шлюз пропускает только шагами сценариев гаджетов.
GADGET_BUY = re.compile(r"/buy_(?:right|left|legs|head|chest|torso)(?:1[0-4]|[1-9])\Z")
GADGET_WEAR = re.compile(r"/wear_[0-9]+_[a-z][0-9]+\Z")
GADGET_UNWEAR = re.compile(r"/unwear_[a-z][0-9]+\Z")
# «К персонажу» из итога метро: выход из забега, если игра его не завершила. Без подтверждения
# шлюз пропускает только шаг сценария метро.
MAIN = re.compile(r"/main\Z")
UPGRADE_CLICK = re.compile(rf"up_(?P<slot>{_UP_SLOTS})_(?P<grade>low|middle|high)(?:_1_accept)?\Z")

TEXT_RULES: tuple[Rule, ...] = (
    Rule(GADGET_BUY, _R),
    Rule(GADGET_WEAR, _R),
    Rule(GADGET_UNWEAR, _R),
    Rule(MAIN, _R),
    *_re(_N, rf"/up_(?:{_UP_SLOTS})\Z"),
    *_re(
        _F,
        r"/changecompany\b",
        r"/profreset\b",
        r"/setfullprofile\b",
        r"/unsetbattle\w*",
        r"/setname\b",
        r"/fullt\Z",
        r"/compactt\Z",
        r"/compacts\Z",
        r"/order_\w+",
        r"/wear_\w+",
        r"/unwear_\w+",
        r"/up_\w+",
        r"/buy_\w+",
        r"/sell_\w+",
        r"/sells_all\Z",
        r"/rem_\w+",
        r"/v_off\Z",
        r"/mouse_name\Z",
        r"/dog_name\Z",
        r"/class\Z",
        r"/keysbuy\Z",
    ),
    *_exact(_F, "🎯 Дартс", "🖲 📚=>🔩", "🖲 🔩=>📚"),
    *_re(
        _D,
        r"/finish\Z",
        r"/dog_rest\Z",
        r"/coins\Z",
        r"/donations?\Z",
        r"/donate\Z",
        r"/fcoins\Z",
        r"/co_\w+",
        r"/sb\d+\Z",
    ),
    *_exact(
        _D,
        "🌐Берёзка",
        "🌐Берёзка другу",
        "+🔵 редкие",
        "+🔴 уникальные",
        "+⚪️ за 🌐",
        "💙Докупить",
    ),
    *_re(
        _R,
        r"/ucon\Z",
        r"/v_(bee|snail|ladybug|ant)\Z",
        r"\+(🍀|👓|🔋|❤️|🔧)(🐀|🐕)\Z",
    ),
    *_exact(
        _R,
        "📚Изучать",
        "🔩Разрабатывать",
        "⚪️ → 🔵",
        "🔵 → 🔴",
        "🎁 за 10🍊",
    ),
    *_exact(
        _N,
        "😎Я",
        "◀️Назад",
        "/compact",
        "/full",
        "/cool",
        "/bonuses",
        "/premium",
        "/richness",
        "/artefacts",
        # Экран пересборки артефакта: условия и кнопка подтверждения — сама кнопка не NAV.
        "/artr_book",
        "/artr_fax",
        "/artr_light",
        "/bag",
        "/pr",
        "/settings",
        "/myorders",
        "⚔Битва",
        "/to_battle",
        "/fb",
        "🏢Офис",
        "🔬Лаборатории",
        "🔬Лаборатория 1",
        "🔬Лаборатория 2",
        "🔬Лаборатория 3",
        "📚Учёные",
        "🔩Разрабы",
        "🛠Мастерская",
        "🧰Сборка",
        "🚦Поездки",
        "🚇Метро",
        "🎒Рюкзак",
        "/inv",
        "/inventory",
        "🎒Сменить",
        "👾Артефакты",
        "🎁Подарки",
        "/gifts",
        "🎉Ачивки",
        "🗜Апгрейды",
        "/upgrades",
        "+⚪️ за 💵",
        "🏪Продать",
        "🧬Вирусы",
        "/viruses",
        "🐝",
        "🐞",
        "🐜",
        "🐌",
        "🐾Петы",
        "/pets",
        "🐀",
        "🐕",
        "/mouse",
        "/dog",
        "❓Петы",
        "🍽️🐀",
        "🍽️🐕",
        "✅🐕",
        "✅🐀",
        "🕸Сеть",
        "🏪Магазин",
        "📱Правая рука",
        "👞Ноги",
        "👕Грудь",
        "⌚️Левая рука",
        "🕶Голова",
        "👔Тело",
        "📈Акции",
        "/stock",
        "📈Купить",
        "/buys",
        "📉Продать",
        "/sells",
        "⚖️Рынок",
        "📥Купить",
        "📤Продать",
        "💧Эфир",
        "🎪Казино",
        "🍹Смузийная",
        "/smoothie",
        "🤑Лотерея",
        "/tickets",
        "🎮Арена",
        "⏳Дела",
        "🍴Еда",
        "/to_eat",
        "🔮Стартап",
        "🔧Профа",
        "🛌Спать",
        "🏛Горбушка",
        "/gorbushka",
        "/subprof",
        "👫Команда",
        "/crew",
        "⏳Задания",
        "📊Ресурсы",
        "/crew_factory",
        "❓Об игре",
        "❓FAQ",
        "❓Битвы",
        "❓Навыки",
        "❓Акции",
        "❓Команды",
        "❓Апгрейды",
        "/topincompany",
        "/freemoney",
        "/companyls",
        "/companyaccount",
        "/ftop",
    ),
    *_re(
        _N,
        r"/help\w*\Z",
        r"/vi_\w+\Z",
        r"/lab[123]\Z",
        r"/top\w*\Z",
        r"/companytop\w*\Z",
        r"/alltops\w*\Z",
        r"/battle(16|19)?\Z",
        r"/crewtop(week)?\Z",
    ),
    *_exact(
        _A,
        "📯Pied Piper",
        "🤖Hooli",
        "⚡️Stark Ind.",
        "☂️Umbrella",
        "🎩Wayne Ent.",
        "☣️Black Mesa",
        "🛡Защита",
        "👍Записаться",
        "👎Выписаться",
        "/levelup",
        "+1 🔨Практика",
        "+1 🎓Теория",
        "+1 🐿Хитрость",
        "+1 🐢Мудрость",
        "⚙️ → 🔩",
        "/dconv",
        "/read_exp",
        "/use_card",
        "💻Работать",
        "/job",
        "🚶Гулять",
        "/walk",
        "🔫Грабить",
        "🌭Хот-дог",
        "🍕Пицца",
        "🍔Бургер",
        "🍌Банан",
        "🍴Есть",
        "/eat",
        "📚Учиться",
        "/learns",
        "📚Конфа",
        "/confa",
        "🖥 Пилить",
        "/dos",
        "⛏Добывать",
        "/harvest",
        "/decline",
        *(v.button for v in VEHICLES.values()),
        "/capitalization",
        "/daily_income",
        "/index_pe",
        "/gt",
        "/del",
        "/tickets_all",
        "🍹Готовить",
        "/dog_wakeup",
        "💵 => 🤑",
        "📚 => 🤑",
        "🔩 => 🤑",
        "⚙️ => 🤑",
    ),
    *_re(
        _A,
        r"/unbox(_\w+)?\Z",
        r"/t_\w+\Z",
        r"/ts_\w+\Z",
        r"join_fight_\w{11}\Z",
        r"/(open|open_all|spring|ch_all)\Z",
        r"/ch\d+\Z",
    ),
)

CALLBACK_RULES: tuple[Rule, ...] = (
    Rule(UPGRADE_CLICK, _R),
    *_re(_N, rf"up_(?:{_UP_SLOTS})_(?:low|middle|high)_decline\Z"),
    *_re(
        _F,
        r"buy_mercenaries_",
        r"take_up_",
        r"fit_",
        r"subprof_select_(?!decline\Z)",
        r"sell_all\Z",
        r"crew_(money|knows|materials)\Z",
        r"mether_buy_money\Z",
        r"pet_select_accept_",
    ),
    *_re(_D, r"mether_buy_coins\Z", r"maze_buf_coins_", r"spring_(roll_coins|regenerate)"),
    # Старт пересборки артефакта обнуляет уровень и 🔥 — без подтверждения проходит только из
    # сценария `artifact_start`.
    *_re(_R, r"crew_change_", r"sells_\w+\Z", r"artr_(book|fax|light)_accept\Z"),
    *_re(
        _N,
        r"cancel_inline\Z",
        r"gorbushka_new_decline\Z",
        r"pet_feast_decline_\w+\Z",
        r"pet_select_decline_\w+\Z",
        r"subprof_select_decline\Z",
        r"maze_nothing\Z",
        r"tasksel_decline\Z",
        r"artr_(book|fax|light)_decline\Z",
    ),
    *_re(
        _A,
        r"maze_(up|down|left|right|start|exit|exit_accept|exit_decline|enter_accept"
        r"|enter_decline|continue|cancel_move|first_aid|first_aid_accept|first_aid_decline"
        r"|chest_accept|chest_decline|npc_low_accept|npc_low_decline|npc_high_accept"
        r"|npc_high_decline|buf_tokens_(fastMove|strong|firstAid))\Z",
        r"gorbushka_(new|new_accept|fight)\Z",
        r"sleep_(7|8|9|10|11|12|Bridge|Hotel)\Z",
        r"sm_drop_[1-5]\Z",
        r"smoothie_accept\Z",
        r"pet_feast_accept_\w+\Z",
        r"spring_roll_smiles\Z",
        r"t_\w+_confirm\Z",
        r"ts_\w+_confirm\Z",
        r"rob_awake_\d+\Z",
        r"tickets_\w+_\d+\Z",
    ),
)


def _classify(value: str, rules: tuple[Rule, ...]) -> CommandClass:
    value = value.strip()
    if not value:
        return CommandClass.FORBIDDEN
    for rule in rules:
        if rule.pattern.match(value):
            return rule.cls
    return CommandClass.FORBIDDEN


def _stock(match: re.Match[str], own_company: str | None) -> CommandClass:
    own = own_company is None or match["company"] == own_company
    return CommandClass.RISKY if own else CommandClass.ACTION


def classify_text(text: str, own_company: str | None = None) -> CommandClass:
    """Класс команды; `own_company` — код своей компании (из профиля) для команд акций."""
    if (m := _STOCK_TEXT.match(text.strip())) is not None:
        return _stock(m, own_company)
    return _classify(text, TEXT_RULES)


def classify_callback(data: str, own_company: str | None = None) -> CommandClass:
    if (m := _STOCK_CALLBACK.match(data.strip())) is not None:
        return _stock(m, own_company)
    return _classify(data, CALLBACK_RULES)


# Какой механике принадлежит команда. Не вручную шлюз пропускает её, только если механика
# включена в настройках (features).
_FEATURE_TEXT: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p), f)
    for p, f in (
        (
            r"(/harvest|⛏Добывать|/job|💻Работать|/learns|📚Учиться|/confa|📚Конфа|/dconv"
            r"|⚙️ → 🔩|/walk|🚶Гулять|🔫Грабить|/eat|🍴Есть)\Z",
            "deeds",
        ),
        (r"(/dos|🖥 Пилить)\Z", "startup"),
        ("(?:" + "|".join(re.escape(v.button) for v in VEHICLES.values()) + r")\Z", "trips"),
        (r"/read_exp\Z", "books"),
        (r"(🌭Хот-дог|🍕Пицца|🍔Бургер|🍌Банан)\Z", "fastfood"),
        (r"(/use_card|/unbox(_\w+)?)\Z", "cards_containers"),
        (r"(/levelup|\+1 🔨Практика|\+1 🎓Теория|\+1 🐿Хитрость|\+1 🐢Мудрость)\Z", "levelup"),
        (r"(/tickets_all|(💵|📚|🔩|⚙️) => 🤑)\Z", "lottery"),
        (r"🍹Готовить\Z", "smoothie"),
        (r"(/capitalization|/daily_income|/index_pe)\Z", "paid_info"),
        (r"(/open|/open_all|/spring|/ch_all|/ch\d+)\Z", "seasonal"),
        (r"/t_\w+\Z", "daily_tasks"),
        (r"/ts_\w+\Z", "daily_tasks"),
        (r"/gt\Z", "tangerine"),
        (r"(👍Записаться|👎Выписаться)\Z", "factory"),
        (r"join_fight_\w{11}\Z", "bulls"),
        (
            r"(📯Pied Piper|🤖Hooli|⚡️Stark Ind\.|☂️Umbrella|🎩Wayne Ent\.|☣️Black Mesa|🛡Защита)\Z",
            "battle",
        ),
        (rf"/(buys|sells)_{_COMPANIES}_[0-9]+\Z", "stocks_dump"),
        (r"(/buy_\w+|/wear_\w+|/unwear_\w+)\Z", "gadgets_buy"),
    )
)
_FEATURE_CALLBACK: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p), f)
    for p, f in (
        (r"gorbushka_(new|new_accept|fight)\Z", "gorbushka"),
        (r"sleep_(\d+|Bridge|Hotel)\Z", "sleep"),
        (r"maze_", "metro"),
        (r"(sm_drop_[1-5]|smoothie_accept)\Z", "smoothie"),
        (rf"buys_{_COMPANIES}\Z", "stocks_dump"),
        (r"pet_feast_accept_", "pet_feast"),
        (r"spring_roll_smiles\Z", "seasonal"),
        (r"t_\w+_confirm\Z", "daily_tasks"),
        (r"ts_\w+_confirm\Z", "daily_tasks"),
        (r"rob_awake_\d+\Z", "robbery_defense"),
        (r"tickets_\w+_\d+\Z", "lottery"),
    )
)
# Не тратят ничего: блок трат (неизвестный исход, рестарт до сверки) их не держит. Проснуться при
# ограблении — единственный способ не потерять 30% 💵; ходы и диалоги в метро ничего не тратят.
_SPEND_FREE_CALLBACK = re.compile(
    r"(?:rob_awake_\d+"
    r"|maze_(?:up|down|left|right|start|continue|cancel_move|enter_decline|exit|exit_accept"
    r"|exit_decline|first_aid|first_aid_accept|first_aid_decline|chest_accept|chest_decline"
    r"|npc_low_accept|npc_low_decline|npc_high_accept|npc_high_decline))\Z"
)


def feature_of_text(text: str) -> str | None:
    value = text.strip()
    return next((f for p, f in _FEATURE_TEXT if p.match(value)), None)


def feature_of_callback(data: str) -> str | None:
    return next((f for p, f in _FEATURE_CALLBACK if p.match(data)), None)


def spends_nothing_callback(data: str) -> bool:
    return _SPEND_FREE_CALLBACK.match(data) is not None
