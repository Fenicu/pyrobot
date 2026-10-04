from app.engine.server_settings import Bound, EngineBounds
from app.engine.settings import Settings


def test_defaults_within_default_bounds() -> None:
    settings = Settings()
    bounds = EngineBounds()
    # Все поля настроек по умолчанию должны укладываться в дефолтные границы
    all_paths = [b.path for b in bounds.bounds()]
    violation = bounds.violation(settings, all_paths)
    assert violation is None


def test_violation_only_for_changed_paths() -> None:
    bounds = EngineBounds(min_request_interval_s_min=1.6)
    # Создаём настройки с недопустимым значением min_request_interval_s = 1.0
    eng = Settings().engine.model_copy(update={"min_request_interval_s": 1.0})
    settings = Settings().model_copy(update={"engine": eng})

    # Если менялись только пути daily.*, нарушения нет (проверяются только изменённые пути)
    assert bounds.violation(settings, ["daily.personal_order"]) is None

    # Если менялся min_request_interval_s (например, на 1.2, что все еще < 1.6),
    # нарушение фиксируется
    eng2 = settings.engine.model_copy(update={"min_request_interval_s": 1.2})
    settings2 = settings.model_copy(update={"engine": eng2})
    v = bounds.violation(settings2, ["engine.min_request_interval_s"])
    assert v is not None
    bound, bound_type, limit = v
    assert bound == Bound("engine.min_request_interval_s", min=1.6, max=None)
    assert bound_type == "min"
    assert limit == 1.6


def test_clamp_brings_values_to_bounds() -> None:
    bounds = EngineBounds(
        min_request_interval_s_min=2.0,
        antiflood_pause_s_min=15.0,
        antiflood_retry_max_max=1,
        action_ttl_s_max=300.0,
    )
    # Задаем значения за границами: слишком маленькие для min и слишком большие для max
    eng = Settings().engine.model_copy(
        update={
            "min_request_interval_s": 1.0,  # < 2.0
            "antiflood_pause_s": 5.0,  # < 15.0
            "antiflood_retry_max": 5,  # > 1
            "action_ttl_s": 600.0,  # > 300.0
        }
    )
    settings = Settings().model_copy(update={"engine": eng})

    clamped = bounds.clamp(settings)
    assert clamped.engine.min_request_interval_s == 2.0
    assert clamped.engine.antiflood_pause_s == 15.0
    assert clamped.engine.antiflood_retry_max == 1
    assert clamped.engine.action_ttl_s == 300.0

    # Если значения уже в пределах границ, clamp возвращает исходный объект
    assert bounds.clamp(clamped) == clamped
