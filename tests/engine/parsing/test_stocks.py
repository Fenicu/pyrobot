from dataclasses import replace

from app.engine.parsing.stocks import Dividends, StockBought, StockScreen, recognize_stocks
from tests.fixtures import game_msg

QUOTES = {"piper": 10, "hooli": 10, "stark": 31, "umbrl": 100, "wayne": 10, "bmesa": 10}
HOLDINGS = {"piper": 3763, "hooli": 1838, "stark": 51, "umbrl": 1232, "wayne": 75, "bmesa": 10}


def test_main_screen_has_quotes_holdings_and_limits() -> None:
    assert recognize_stocks(game_msg("stocks", 3624065)) == [
        StockScreen(
            screen="main",
            quotes=QUOTES,
            holdings=HOLDINGS,
            money=2364,
            open_hour=8,
            close_hour=22,
            min_buy=11,
            max_sell=80,
            reserve=100,
        )
    ]


def test_buy_and_sell_screens() -> None:
    [buy] = recognize_stocks(game_msg("stocks", 3624067))
    assert isinstance(buy, StockScreen)
    assert (buy.screen, buy.min_buy, buy.max_sell, buy.reserve) == ("buy", 11, None, 100)
    [sell] = recognize_stocks(game_msg("stocks", 3592131))
    assert isinstance(sell, StockScreen)
    assert (sell.screen, sell.max_sell, sell.money, sell.quotes["stark"]) == ("sell", 80, 670, 36)


def test_bought_and_dividends() -> None:
    assert recognize_stocks(game_msg("stocks", 3606034)) == [
        StockBought(company="hooli", price=11, n=76, money=100, shares=1838)
    ]
    assert recognize_stocks(game_msg("stocks", 3564237)) == [
        StockBought(company="umbrl", price=67, n=11, money=110, shares=1232)
    ]
    assert recognize_stocks(game_msg("stocks", 3621194)) == [Dividends(amount=1065)]
    assert recognize_stocks(game_msg("stocks", 3582720)) == [Dividends(amount=236)]
    assert recognize_stocks(game_msg("stocks", 3624068)) == [
        StockBought(company="stark", price=31, n=70, money=124, shares=121)
    ]


def test_main_screen_at_night_closed() -> None:
    holdings = {**HOLDINGS, "stark": 121}
    assert recognize_stocks(game_msg("stocks", 3624609)) == [
        StockScreen(
            screen="main",
            quotes=QUOTES,
            holdings=holdings,
            money=867,
            open_hour=8,
            close_hour=22,
            closed=True,
            min_buy=11,
            max_sell=80,
            reserve=100,
        )
    ]


def test_main_screen_needs_all_quotes() -> None:
    msg = game_msg("stocks", 3624065)
    assert msg.text is not None
    broken = replace(msg, text=msg.text.replace("🤖Hooli - 10 💵 за шт.\n", ""))
    assert recognize_stocks(broken) == []


def test_main_screen_needs_valid_holdings() -> None:
    msg = game_msg("stocks", 3624065)
    assert msg.text is not None
    full = "🤖Hooli - 1\xa0838 шт. на $18\xa0380 💵"
    truncated = "🤖Hooli - 1\xa0838 шт."
    broken = replace(msg, text=msg.text.replace(full, truncated))
    assert recognize_stocks(broken) == []
