from __future__ import annotations

from collections.abc import Mapping


def pick_stock(
    quotes: Mapping[str, int], min_buy: int, max_sell: int, margin: int, own: str
) -> tuple[str, int] | None:
    """Самая дорогая чужая акция, которую можно купить и потом продать с запасом до лимита.

    Акции своей компании `own` бот сам не покупает: ими распоряжается CEO компании."""
    candidates = [
        (price, company)
        for company, price in quotes.items()
        if company != own and min_buy <= price <= max_sell - margin
    ]
    if not candidates:
        return None
    price, company = max(candidates)
    return company, price


def dump_size(money: int, keep: int, reserve: int, price: int) -> int:
    # Комиссия брокера — 1💵 за акцию; после покупки игра требует не меньше reserve.
    return max((money - max(keep, reserve)) // (price + 1), 0)
