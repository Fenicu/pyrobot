"""Публичная схема `/state` (`PublicState`) держит реальную форму снимка и не отстаёт от модели
движка: новое поле `CharacterState` без поля в публичной схеме валит тест."""

import functools
import json
import operator
import types
from datetime import datetime
from pathlib import Path
from typing import Any, Union, get_args, get_origin

from app.api.routes_state import StateOut
from app.api.state_schema import MetroRunRefOut, Observed, PublicState
from app.engine.state.model import CharacterState, MetroRunRef, Obs, dump_state

PROD = Path(__file__).resolve().parent.parent / "fixtures" / "api" / "state.json"


def _public(tp: Any) -> Any:
    """Тип поля движка в терминах публичной схемы: `Obs[T]` → `Observed[T]`."""
    if tp is MetroRunRef:
        return MetroRunRefOut
    meta = getattr(tp, "__pydantic_generic_metadata__", None)
    if meta and meta["origin"] is Obs:
        return Observed[_public(meta["args"][0])]  # type: ignore[valid-type]
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        return functools.reduce(operator.or_, (_public(a) for a in get_args(tp)))
    if origin is dict:
        key, value = get_args(tp)
        return dict[key, _public(value)]  # type: ignore[valid-type]
    return tp


def test_public_state_follows_engine_model() -> None:
    engine = {k: f for k, f in CharacterState.model_fields.items() if k != "applied"}
    assert list(PublicState.model_fields) == list(engine)
    for name, field in engine.items():
        assert PublicState.model_fields[name].annotation == _public(field.annotation), name
        assert not PublicState.model_fields[name].is_required(), name


def test_real_prod_snapshot_validates() -> None:
    # Снимок с прода 27.09: порядок ключей из JSONB, без поля `lottery`.
    body = json.loads(PROD.read_text(encoding="utf-8"))
    assert "lottery" not in body["state"]
    out = StateOut.model_validate(body)
    assert out.state.money is not None and out.state.money.value == 47
    assert out.state.lottery is None


def test_full_default_and_empty_snapshots_validate() -> None:
    StateOut.model_validate(
        {"version": 0, "now": datetime.now().isoformat(), "state": {}, "stale": []}
    )
    full = {k: v for k, v in dump_state(CharacterState()).items() if k != "applied"}
    assert PublicState.model_validate(full).model_dump(mode="json") == full
