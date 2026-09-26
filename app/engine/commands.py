from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class CommandClass(StrEnum):
    NAV = "nav"
    ACTION = "action"
    RISKY = "risky"
    FORBIDDEN = "forbidden"
    DONATE = "donate"


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
_COMPANIES = r"(?:piper|hooli|stark|umbrl|wayne)"

TEXT_RULES: tuple[Rule, ...] = (
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
        r"/main\Z",
        r"/class\Z",
        r"/dos\Z",
        r"/keysbuy\Z",
    ),
    *_exact(_F, "🖥 Пилить", "🎯 Дартс", "🖲 📚=>🔩", "🖲 🔩=>📚"),
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
        r"/buys_bmesa_\d+\Z",
        r"/sells_bmesa_\d+\Z",
        r"\+(🍀|👓|🔋|❤️|🔧)(🐀|🐕)\Z",
    ),
    *_exact(
        _R,
        "📚Изучать",
        "🔩Разрабатывать",
        "⚪️ → 🔵",
        "🔵 → 🔴",
        "🚲Велик",
        "🚕Тачка",
        "🚃Трамвай",
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
        "⛏Добывать",
        "/harvest",
        "/decline",
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
        rf"/buys_{_COMPANIES}_\d+\Z",
        rf"/sells_{_COMPANIES}_\d+\Z",
        r"/unbox(_\w+)?\Z",
        r"/t_\w+\Z",
        r"join_fight_\w{11}\Z",
        r"/(open|open_all|spring|ch_all)\Z",
        r"/ch\d+\Z",
    ),
)

CALLBACK_RULES: tuple[Rule, ...] = (
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
    *_re(_R, r"crew_change_", r"buys_bmesa\Z", r"sells_\w+\Z"),
    *_re(
        _N,
        r"cancel_inline\Z",
        r"gorbushka_new_decline\Z",
        r"pet_feast_decline_\w+\Z",
        r"pet_select_decline_\w+\Z",
        r"subprof_select_decline\Z",
        r"maze_nothing\Z",
    ),
    *_re(
        _A,
        r"maze_(up|down|left|right|start|exit|exit_accept|exit_decline|enter_accept"
        r"|enter_decline|continue|cancel_move|first_aid|first_aid_accept|first_aid_decline"
        r"|chest_accept|chest_decline|npc_low_accept|npc_low_decline|npc_high_accept"
        r"|npc_high_decline|buf_tokens_(fastMove|strong|firstAid))\Z",
        r"gorbushka_(new|new_accept|fight)\Z",
        r"sleep_(7|8|9|10|11|12)\Z",
        r"sm_drop_[1-5]\Z",
        r"smoothie_accept\Z",
        rf"buys_{_COMPANIES}\Z",
        r"pet_feast_accept_\w+\Z",
        r"spring_roll_smiles\Z",
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


def classify_text(text: str) -> CommandClass:
    return _classify(text, TEXT_RULES)


def classify_callback(data: str) -> CommandClass:
    return _classify(data, CALLBACK_RULES)
