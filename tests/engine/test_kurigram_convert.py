from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from app.engine.settings import ChatsSection
from app.engine.transport.kurigram import ChatFilter, has_join_fight, to_incoming

T0 = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _btn(**kw: object) -> NS:
    base = dict(
        text="⬆️",
        callback_data=None,
        url=None,
        switch_inline_query=None,
        switch_inline_query_current_chat=None,
    )
    base.update(kw)
    return NS(**base)


def _m(**kw: object) -> NS:
    base = dict(
        id=10,
        chat=NS(id=227859379),
        from_user=NS(id=227859379),
        text="карта",
        caption=None,
        reply_markup=None,
        date=T0,
        edit_date=None,
        outgoing=False,
    )
    base.update(kw)
    return NS(**base)


def test_inline_markup_and_new() -> None:
    kb = NS(inline_keyboard=[[_btn(callback_data=b"maze_up")]])
    msg = to_incoming(_m(reply_markup=kb), kind="new", received_at=T0)
    assert msg.revision == 0 and msg.kind == "new"
    assert msg.button("maze_up") is not None and msg.inline[0].row == 0
    assert msg.recovered is False


def test_edit_uses_edit_date_and_reply_kb() -> None:
    edited = T0 + timedelta(seconds=30)
    kb = NS(keyboard=[["😎Я", NS(text="⚔Битва")]])
    msg = to_incoming(_m(edit_date=edited, reply_markup=kb), kind="edit", received_at=edited)
    assert msg.date == edited and msg.revision == int(edited.timestamp())
    assert msg.reply_kb == (("😎Я", "⚔Битва"),)


def test_recovered_flag_and_naive_dates() -> None:
    naive = datetime(2026, 1, 1, 12, 0)
    received = datetime(2026, 1, 1, 12, 0).astimezone(UTC) + timedelta(minutes=5)
    msg = to_incoming(_m(date=naive), kind="new", received_at=received)
    assert msg.date.tzinfo is not None and msg.recovered is True
    fresh = to_incoming(
        _m(date=naive), kind="new", received_at=datetime(2026, 1, 1, 12, 0).astimezone(UTC)
    )
    assert fresh.recovered is False


def test_chat_filter_and_join_fight() -> None:
    f = ChatFilter.from_settings(ChatsSection(bulls_invite_chat_id=-100500))
    assert f.accepts(_m())
    assert f.accepts(_m(chat=NS(id=-1001109615116), from_user=NS(id=376592453)))
    assert not f.accepts(_m(chat=NS(id=-1001109615116), from_user=NS(id=1)))
    invite_kb = NS(
        inline_keyboard=[[_btn(text="Бой", switch_inline_query="join_fight_I16YW9RrvSq")]]
    )
    invite = _m(chat=NS(id=-100500), from_user=NS(id=5), reply_markup=invite_kb, text="Нажми")
    text_only = _m(chat=NS(id=-100500), from_user=NS(id=5), text="join_fight_I16YW9RrvSq")
    bad_kb = NS(inline_keyboard=[[_btn(text="Бой", switch_inline_query="join_fight_x; rm")]])
    malformed = _m(chat=NS(id=-100500), from_user=NS(id=5), reply_markup=bad_kb)
    assert has_join_fight(invite) and f.accepts(invite)
    assert not has_join_fight(text_only) and not f.accepts(text_only)
    assert not f.accepts(malformed)
    assert not f.accepts(_m(chat=NS(id=-100500), from_user=NS(id=5), text="привет"))
    assert not f.accepts(_m(chat=NS(id=42)))


def test_recovered_boundary_is_exactly_60s() -> None:
    at_limit = to_incoming(_m(date=T0), kind="new", received_at=T0 + timedelta(seconds=60))
    assert at_limit.recovered is False
    over = to_incoming(
        _m(date=T0), kind="new", received_at=T0 + timedelta(seconds=60, microseconds=1)
    )
    assert over.recovered is True


def test_created_at_kept_for_edits() -> None:
    edited = T0 + timedelta(seconds=30)
    msg = to_incoming(_m(edit_date=edited), kind="edit", received_at=edited)
    assert (msg.created_at, msg.date, msg.origin) == (T0, edited, T0)
    new = to_incoming(_m(), kind="new", received_at=T0)
    assert (new.created_at, new.origin) == (T0, T0)


def test_new_message_already_edited_uses_edit_date() -> None:
    # Догон истории отдаёт правленое сообщение как новое: время события — время правки.
    edited = T0 + timedelta(seconds=30)
    msg = to_incoming(_m(edit_date=edited), kind="new", received_at=edited)
    assert (msg.date, msg.created_at, msg.origin) == (edited, T0, T0)
    assert msg.revision == int(edited.timestamp())
