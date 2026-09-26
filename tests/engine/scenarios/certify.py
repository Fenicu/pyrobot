from collections.abc import Callable
from typing import Any

F = Callable[..., Any]


def certifies(*names: str) -> Callable[[F], F]:
    """Отмечает тест как покрывающий переходы сценариев `names` (см. test_registry)."""

    def mark(fn: F) -> F:
        fn.__certifies__ = names  # type: ignore[attr-defined]
        return fn

    return mark
