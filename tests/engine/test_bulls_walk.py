"""Встреча с биржевиком на ночной прогулке: «⚔Драться» и приглашение в чат через инлайн-режим."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.engine.bulls_walk import BullsWalk
from app.engine.bus import Delivery
from app.engine.gametime import MSK
from app.engine.gateway.gateway import RECONCILE_REASON
from app.engine.notify import Level
from app.engine.parsing import default_parser
from app.engine.settings import ChatsSection, Settings
from app.engine.state.model import BusyState, CharacterState, MetroRunRef, Obs
from app.engine.transport.fake import Sent
from app.engine.types import Button, IncomingMessage
from tests.engine.gateway_rig import LIVE, Rig
from tests.engine.helpers import make_msg, until
from tests.engine.parsing.test_bulls import FIGHT_BUTTONS, WALK_BEAR, WALK_BULL

INVITES = -1001149209877
INVITES_LIVE = Settings(
    engine=LIVE.engine, chats=LIVE.chats.model_copy(update={"bulls_invite_chat_id": INVITES})
)
OFFER = 3629542
CODE = "join_fight_GXnJJ0QNK2K"
# Ночь по Москве: встречи приходят с 23 до 8.
NIGHT = datetime(2026, 10, 3, 1, 42, tzinfo=MSK).astimezone(UTC)
PARSER = default_parser(ChatsSection())


class Frozen:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def now(self) -> datetime:
        return self.at

    def monotonic(self) -> float:
        return time.monotonic()


class Notes:
    def __init__(self) -> None:
        self.items: list[tuple[Level, str, str]] = []

    async def notify(self, level: Level, code: str, text: str) -> None:
        self.items.append((level, code, text))

    @property
    def codes(self) -> list[tuple[Level, str]]:
        return [(level, code) for level, code, _ in self.items]


class WalkRig:
    def __init__(self, settings: Settings = INVITES_LIVE) -> None:
        self.clock = Frozen(NIGHT + timedelta(seconds=3))
        self.gw = Rig(settings, clock=self.clock)
        self.notes = Notes()
        self.state = CharacterState()
        self.released = 0
        self.reaction = BullsWalk(
            gateway=self.gw.gw,
            settings=self.gw.settings,
            state=lambda: self.state,
            notifier=self.notes,
            clock=self.clock,
            answer_timeout_s=0.3,
            follow_s=0.3,
            released=self._released,
        )
        self.tasks: list[asyncio.Task[None]] = []

    def _released(self) -> None:
        self.released += 1

    def start(self) -> None:
        self.gw.start()
        self.tasks.append(asyncio.create_task(self.reaction.run()))

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.gw.stop()

    async def deliver(self, msg: IncomingMessage, *, reactable: bool = True) -> None:
        """Как шина: шлюз (по нему ждут ответ на клик), затем реакция."""
        self.gw.latest[(msg.chat_id, msg.msg_id)] = msg
        self.gw.jid += 1
        delivery = Delivery(msg, tuple(PARSER.parse(msg)), 0, self.gw.jid, reactable)
        await self.gw.gw.on_delivery(delivery)
        await self.reaction.on_delivery(delivery)

    def answer_with(self, *answers: IncomingMessage) -> None:
        """Игра отвечает на «⚔Драться» этими сообщениями (свежая дата — после клика)."""

        async def responder(rec: Sent) -> None:
            if rec.kind != "click":
                return
            for answer in answers:
                await self.deliver(replace(answer, date=datetime.now(UTC)))

        self.gw.transport.responder = responder

    def clicks(self) -> list[str]:
        return [s.payload for s in self.gw.transport.sent if s.kind == "click"]

    def invites(self) -> list[tuple[int, str]]:
        return [(s.chat_id, s.payload) for s in self.gw.transport.sent if s.kind == "inline"]

    async def settled(self) -> None:
        await until(lambda: self.reaction.idle, 3.0)


@pytest.fixture
async def rig() -> AsyncIterator[WalkRig]:
    r = WalkRig()
    r.start()
    try:
        yield r
    finally:
        await r.stop()


def offer(text: str = WALK_BULL, *, age: timedelta = timedelta(seconds=3)) -> IncomingMessage:
    moment = NIGHT + timedelta(seconds=3) - age
    msg = make_msg(text, msg_id=OFFER, buttons=FIGHT_BUTTONS, date=moment)
    return replace(msg, created_at=moment)


def edited(text: str, *buttons: Button) -> IncomingMessage:
    return make_msg(text, msg_id=OFFER, kind="edit", revision=1, buttons=buttons)


def new(text: str, *buttons: Button, msg_id: int = OFFER + 1) -> IncomingMessage:
    return make_msg(text, msg_id=msg_id, buttons=buttons)


SHARE_SWITCH = Button("⚔Позвать друзей", 0, 0, switch=CODE)
SHARE_CHOSEN = Button("⚔Позвать друзей", 0, 0, switch_chosen=CODE)


@pytest.mark.parametrize(
    "answer",
    [
        edited("Ты решил драться. Позови друзей!", SHARE_SWITCH),
        new("Позови друзей на помощь!", SHARE_CHOSEN),
        new(f"Отправь друзьям код {CODE}, чтобы они присоединились."),
    ],
    ids=["edit_switch", "new_chosen_chat", "code_in_text"],
)
async def test_fight_accepted_and_invite_posted(rig: WalkRig, answer: IncomingMessage) -> None:
    rig.answer_with(answer)
    await rig.deliver(offer())
    await rig.settled()
    assert rig.clicks() == ["fight_accept"]
    assert rig.invites() == [(INVITES, CODE)]
    assert rig.notes.codes == [("info", "bulls_walk_invited")]


async def test_share_in_second_answer(rig: WalkRig) -> None:
    # Игра правит предложение, а кнопку приглашения присылает следующим сообщением.
    rig.answer_with(edited("Ты решил драться."), new("Позови друзей!", SHARE_CHOSEN))
    await rig.deliver(offer(WALK_BEAR))
    await rig.settled()
    assert rig.invites() == [(INVITES, CODE)]
    assert rig.notes.codes == [("info", "bulls_walk_invited")]


async def test_unknown_answer_stops_and_warns(rig: WalkRig) -> None:
    rig.answer_with(edited("Ты встал в боевую стойку и ждёшь.", Button("👊Ударить", 0, 0, "hit")))
    await rig.deliver(offer())
    await rig.settled()
    # Больше ничего не жмёт и никуда не пишет.
    assert rig.clicks() == ["fight_accept"] and rig.invites() == []
    [(level, code, text)] = rig.notes.items
    assert (level, code) == ("warn", "bulls_walk_unknown")
    assert "Ты встал в боевую стойку и ждёшь." in text


async def test_no_answer_warns(rig: WalkRig) -> None:
    await rig.deliver(offer())
    await rig.settled()
    assert rig.clicks() == ["fight_accept"] and rig.invites() == []
    assert rig.notes.codes == [("warn", "bulls_walk_unknown")]
    # Исход траты неизвестен: до сверки состояния траты стоят, как после любого действия.
    assert rig.gw.gw.spending_blocked == RECONCILE_REASON


async def test_invite_refused_warns(rig: WalkRig) -> None:
    from app.engine.transport.base import TransportRejected

    async def responder(rec: Sent) -> None:
        if rec.kind == "click":
            rig.gw.transport.fail_with.append(TransportRejected("no_inline_results"))
            await rig.deliver(replace(new("Позови друзей!", SHARE_CHOSEN), date=datetime.now(UTC)))

    rig.gw.transport.responder = responder
    await rig.deliver(offer())
    await rig.settled()
    assert rig.invites() == []
    [(level, code, text)] = rig.notes.items
    assert (level, code) == ("warn", "bulls_walk_unknown") and "no_inline_results" in text


async def test_offer_handled_once(rig: WalkRig) -> None:
    rig.answer_with(edited("Ты решил драться. Позови друзей!", SHARE_SWITCH))
    await rig.deliver(offer())
    await rig.deliver(offer())
    await rig.settled()
    await rig.deliver(offer())
    await rig.settled()
    assert rig.clicks() == ["fight_accept"] and rig.invites() == [(INVITES, CODE)]


def _at(**fields: Any) -> CharacterState:
    return CharacterState(
        **{k: Obs(value=v, at=NIGHT - timedelta(minutes=5)) for k, v in fields.items()}
    )


@pytest.mark.parametrize(
    "state",
    [
        _at(busy=BusyState(activity="sleep_Hotel", until=NIGHT + timedelta(hours=3))),
        _at(busy=BusyState(activity="bulls", until=NIGHT + timedelta(minutes=4))),
        _at(
            metro_message=MetroRunRef(
                message_id=5, battle_at=Obs(value=NIGHT + timedelta(hours=6), at=NIGHT)
            )
        ),
        _at(bulls_won_at=NIGHT - timedelta(hours=1)),
    ],
    ids=["asleep", "fighting", "metro", "won_tonight"],
)
async def test_no_fight_when_character_cannot(rig: WalkRig, state: CharacterState) -> None:
    rig.state = state
    await rig.deliver(offer())
    await rig.settled()
    assert rig.gw.transport.sent == [] and rig.notes.items == []


async def test_walk_ending_does_not_block(rig: WalkRig) -> None:
    rig.state = _at(busy=BusyState(activity="walk", until=NIGHT + timedelta(seconds=10)))
    rig.answer_with(edited("Ты решил драться. Позови друзей!", SHARE_SWITCH))
    await rig.deliver(offer())
    await rig.settled()
    assert rig.clicks() == ["fight_accept"]


async def test_last_night_win_does_not_block(rig: WalkRig) -> None:
    rig.state = _at(bulls_won_at=NIGHT - timedelta(hours=20))
    rig.answer_with(edited("Ты решил драться. Позови друзей!", SHARE_SWITCH))
    await rig.deliver(offer())
    await rig.settled()
    assert rig.clicks() == ["fight_accept"]


async def _settings(rig: WalkRig, section: str, **values: object) -> None:
    await rig.gw.settings.update(
        lambda s: s.model_copy(update={section: getattr(s, section).model_copy(update=values)}),
        changed_by="test",
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda r: _settings(r, "chats", bulls_invite_chat_id=None),
        lambda r: _settings(r, "features", bulls=False),
        lambda r: _settings(r, "engine", paused=True),
    ],
    ids=["no_invite_chat", "bulls_off", "paused"],
)
async def test_no_fight_when_off(rig: WalkRig, change: Callable[[WalkRig], Any]) -> None:
    await change(rig)
    await rig.deliver(offer())
    await rig.settled()
    assert rig.gw.transport.sent == [] and rig.notes.items == []


async def test_dry_run_click_suppressed_quietly() -> None:
    dry = INVITES_LIVE.model_copy(
        update={"engine": INVITES_LIVE.engine.model_copy(update={"mode": "dry_run"})}
    )
    r = WalkRig(dry)
    r.start()
    try:
        await r.deliver(offer())
        await r.settled()
        assert r.gw.transport.sent == [] and r.notes.items == []
    finally:
        await r.stop()


@pytest.mark.parametrize(
    ("msg", "reactable"),
    [
        (offer(age=timedelta(seconds=178)), True),
        (offer(), False),
        (replace(offer(), outgoing=True), True),
        (replace(offer(), kind="edit", revision=5), True),
    ],
    ids=["expired", "not_reactable", "outgoing", "edit"],
)
async def test_offer_ignored(rig: WalkRig, msg: IncomingMessage, reactable: bool) -> None:
    await rig.deliver(msg, reactable=reactable)
    await rig.settled()
    assert rig.gw.transport.sent == [] and rig.notes.items == []


# Приглашение в чате приглашений, как его видят свои аккаунты (прод 06.10, msg 704248).
INVITE_TEXT = (
    "Я встретил банду 🐻Медведей и хочу с ними сразиться. Помоги мне!\n"
    "Нажми на кнопку, выбери бота игры и отправь ему полученное сообщение."
)


def test_own_invite_joined_by_other_accounts() -> None:
    from types import SimpleNamespace as NS

    from app.engine.parsing.bulls import BullsInvite
    from app.engine.planner.decide import decide
    from app.engine.state.model import load_state
    from app.engine.state.reducer import StateReducer
    from app.engine.transport.kurigram import ChatFilter
    from tests.engine.planner.test_obligations import act, msk, only
    from tests.engine.planner.test_obligations import state as planner_state

    now = msk(23, 30)
    button = Button("⚔Присоединиться", 0, 0, switch=CODE)
    # Другой свой аккаунт видит сообщение инициатора (отправлено через инлайн-режим бота игры).
    seen = replace(
        make_msg(INVITE_TEXT, chat_id=INVITES, msg_id=704248, buttons=(button,)),
        date=now - timedelta(seconds=30),
        from_id=267519921,
    )
    chats = ChatsSection(bulls_invite_chat_id=INVITES)
    raw_invite = NS(
        chat=NS(id=INVITES),
        from_user=NS(id=267519921),
        reply_markup=NS(
            inline_keyboard=[
                [
                    NS(
                        text=button.text,
                        callback_data=None,
                        url=None,
                        switch_inline_query=CODE,
                        switch_inline_query_current_chat=None,
                        switch_inline_query_chosen_chat=None,
                        copy_text=None,
                    )
                ]
            ]
        ),
    )
    assert ChatFilter.from_settings(chats).accepts(raw_invite)
    parser = default_parser(chats)
    events = parser.parse(seen)
    assert events == [BullsInvite(code=CODE)]
    reduced = load_state(StateReducer().apply({}, seen, events))
    assert reduced.bulls_invite is not None
    decision = decide(planner_state(now, bulls_invite=reduced.bulls_invite), only("bulls"), now)
    assert act(decision) == ("bulls_join", {"code": CODE})
    # Сам инициатор своё приглашение не подхватывает: у него оно исходящее.
    assert parser.parse(replace(seen, outgoing=True)) == []


@pytest.mark.parametrize(
    "answer",
    [
        edited("Позови друзей!", Button("⚔Позвать", 0, 0, switch="premium")),
        new("Позови друзей!", Button("⚔Позвать", 0, 0, switch_chosen="join_fight_x")),
        new("Позови друзей!", Button("Код", 0, 0, copy=f"{CODE} и ещё")),
    ],
    ids=["other_query", "short_code", "copy_with_tail"],
)
async def test_only_invite_code_is_shared(rig: WalkRig, answer: IncomingMessage) -> None:
    rig.answer_with(answer)
    await rig.deliver(offer())
    await rig.settled()
    assert rig.invites() == []
    assert rig.notes.codes == [("warn", "bulls_walk_unknown")]


async def test_planner_held_until_answer_and_follow_window_end(rig: WalkRig) -> None:
    rig.answer_with(edited("Ты встал в боевую стойку."))
    assert not rig.reaction.holding
    await rig.deliver(offer())
    # С постановки встречи: планировщик уже не шлёт /walk.
    assert rig.reaction.holding
    await until(lambda: rig.clicks() == ["fight_accept"])
    await asyncio.sleep(0.15)
    # Ответ разобран, но окно следующих сообщений ещё идёт.
    assert rig.reaction.holding and rig.released == 0
    await rig.settled()
    assert not rig.reaction.holding and rig.released == 1


async def test_skipped_offer_releases_planner_at_once(rig: WalkRig) -> None:
    await _settings(rig, "chats", bulls_invite_chat_id=None)
    await rig.deliver(offer())
    await rig.settled()
    assert rig.released == 1 and rig.gw.transport.sent == []


async def test_reply_to_own_command_does_not_confirm_click(rig: WalkRig) -> None:
    async def responder(rec: Sent) -> None:
        if rec.kind != "click":
            return
        # Своя команда (например, /walk) и ответ игры на неё — не ответ на клик.
        await rig.deliver(replace(new("/walk", msg_id=OFFER + 1), outgoing=True))
        await rig.deliver(
            replace(new("Ты отправился гулять. Вернёшься через 5 минут.", msg_id=OFFER + 2))
        )
        await rig.deliver(new(f"Позови друзей: {CODE}", msg_id=OFFER + 3))

    rig.gw.transport.responder = responder
    await rig.deliver(offer())
    await rig.settled()
    [click] = [r for r in rig.gw.store.rows.values() if r.req.data == "fight_accept"]
    assert click.status.value == "outcome_unknown"
    assert rig.invites() == []
    [(level, code, text)] = rig.notes.items
    assert (level, code) == ("warn", "bulls_walk_unknown")
    assert "Ты отправился гулять" not in text
