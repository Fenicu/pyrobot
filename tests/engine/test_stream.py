from datetime import UTC, datetime

from app.engine.bus import Delivery
from app.engine.commands import CommandClass
from app.engine.gateway.types import ActionKind, ActionRequest, ActionStatus, Source
from app.engine.memory import MemoryActionStore
from app.engine.parsing.refusals import Busy
from app.engine.planner.store import MemoryPlannerStore
from app.engine.planner.types import Act, Wait
from app.engine.stream import (
    EventStream,
    PublishingActionStore,
    PublishingPlannerStore,
    StreamFeed,
)
from app.engine.types import Button
from tests.engine.helpers import GAME, make_msg

AT = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def test_ids_are_epoch_and_sequence() -> None:
    stream = EventStream(epoch="e1")
    stream.publish("notification", {"code": "a"})
    stream.publish("notification", {"code": "b"})
    assert [stream.event_id(e.seq) for e in stream.history()] == ["e1:1", "e1:2"]
    # reset нового подключения получает id последнего события.
    assert stream.event_id(stream.subscribe(None).at) == "e1:2"


def test_resume_replays_after_last_id() -> None:
    stream = EventStream(epoch="e1")
    for code in "abc":
        stream.publish("notification", {"code": code})
    sub = stream.subscribe("e1:1")
    assert sub.reset is None
    assert [e.data["code"] for e in sub.replay] == ["b", "c"]
    stream.publish("notification", {"code": "d"})
    assert sub.queue.get_nowait().data == {"code": "d"}


def test_reset_reasons() -> None:
    stream = EventStream(epoch="e1", history=2)
    for code in "abcd":
        stream.publish("notification", {"code": code})
    assert stream.subscribe(None).reset == "new"
    assert stream.subscribe("e0:3").reset == "epoch"
    assert stream.subscribe("garbage").reset == "unknown"
    assert stream.subscribe("e1:9").reset == "unknown"
    # Хранятся только 3 и 4: после 1 пропал бы 2.
    assert stream.subscribe("e1:1").reset == "evicted"
    ok = stream.subscribe("e1:2")
    assert ok.reset is None and [e.seq for e in ok.replay] == [3, 4]
    assert stream.subscribe("e1:4").replay == []


def test_lagging_subscriber_dropped_without_blocking() -> None:
    stream = EventStream(epoch="e1", queue_size=2)
    slow = stream.subscribe(None)
    fast = stream.subscribe(None)
    stream.publish("notification", {"code": "a"})
    stream.publish("notification", {"code": "b"})
    fast.queue.get_nowait()
    fast.queue.get_nowait()
    stream.publish("notification", {"code": "c"})
    assert slow.dropped and not fast.dropped
    assert stream.subscribers == 1
    assert fast.queue.get_nowait().data == {"code": "c"}
    stream.unsubscribe(slow)
    stream.unsubscribe(fast)
    assert stream.subscribers == 0


async def test_action_store_publishes_lifecycle() -> None:
    stream = EventStream(epoch="e1")
    store = PublishingActionStore(MemoryActionStore(), stream)
    req = ActionRequest(kind=ActionKind.SEND, chat_id=GAME, text="/job", source=Source.MANUAL)
    action_id = await store.create(req, CommandClass.ACTION, ActionStatus.INTENT)
    await store.update(action_id, status=ActionStatus.CONFIRMED, reason="activity_started")
    first, second = stream.history()
    assert (first.type, first.data["status"], first.data["text"]) == ("action", "intent", "/job")
    assert first.data["source"] == "manual" and first.data["id"] == action_id
    assert second.data == {"id": action_id, "status": "confirmed", "reason": "activity_started"}
    assert await store.unreconciled() == []


async def test_planner_store_publishes_decisions_and_runs() -> None:
    stream = EventStream(epoch="e1")
    store = PublishingPlannerStore(MemoryPlannerStore(), stream)
    decision = await store.record(AT, Act("book", {}, "book_ready"))
    await store.record(AT, Wait(None, "idle"))
    run = await store.run_started(decision, "book", {"item": "book"}, AT)
    await store.run_finished(run, "done", "book_read", AT)
    manual = {"requested": {}, "key": "k", "by": "admin", "at": AT}
    queued, created = await store.run_requested("card", {}, **manual)
    await store.run_requested("card", {}, **manual)
    await store.run_begin(queued, AT)
    assert created
    assert [(e.type, e.data.get("status") or e.data.get("kind")) for e in stream.history()] == [
        ("decision", "act"),
        ("decision", "wait"),
        ("scenario_run", "running"),
        ("scenario_run", "done"),
        ("scenario_run", "queued"),
        ("scenario_run", "running"),
    ]


async def test_feed_publishes_messages_and_changed_state() -> None:
    stream = EventStream(epoch="e1")
    state: dict[str, object] = {"money": {"value": 1}, "applied": ["x"]}
    feed = StreamFeed(stream, lambda: state)
    msg = make_msg("❗️Ты занят", msg_id=7)
    await feed.on_delivery(Delivery(msg, (Busy(left_s=5),), 1, 11))
    state = {"money": {"value": 2}, "busy": {"value": 5}, "applied": ["x", "y"]}
    await feed.on_delivery(Delivery(msg, (), 2, 12))
    await feed.on_delivery(Delivery(msg, (), 2, 13))
    events = [(e.type, e.data) for e in stream.history()]
    assert [t for t, _ in events] == ["message", "state", "message", "state", "message"]
    message = events[0][1]
    assert (message["journal_id"], message["msg_id"], message["text"]) == (11, 7, "❗️Ты занят")
    assert message["events"] == [{"kind": "busy", "left_s": 5}]
    assert events[1][1] == {"version": 1, "changed": {"money": {"value": 1}}}
    assert events[3][1] == {
        "version": 2,
        "changed": {"money": {"value": 2}, "busy": {"value": 5}},
    }


async def test_feed_message_carries_buttons_like_journal() -> None:
    stream = EventStream(epoch="e1")
    feed = StreamFeed(stream, lambda: {})
    buttons = (
        Button("Под мостом - 0 💵", 0, 0, "sleep_Bridge"),
        Button("В отеле - 213 💵", 1, 0, "sleep_Hotel"),
    )
    msg = make_msg("Где собираешься спать?", msg_id=3626304, revision=1790535904, buttons=buttons)
    await feed.on_delivery(Delivery(msg, (), 0, 776))
    await feed.on_delivery(Delivery(make_msg("без кнопок", msg_id=8), (), 0, 777))
    first, second = (e.data for e in stream.history())
    assert list(first) == [
        "journal_id",
        "chat_id",
        "msg_id",
        "revision",
        "kind",
        "date",
        "outgoing",
        "text",
        "events",
        "markup",
    ]
    assert (first["chat_id"], first["msg_id"], first["revision"]) == (GAME, 3626304, 1790535904)
    # Та же форма, что у элемента /journal (кадр с прода 27.09).
    assert first["markup"] == {
        "inline": [
            ["Под мостом - 0 💵", 0, 0, "sleep_Bridge", None, None],
            ["В отеле - 213 💵", 1, 0, "sleep_Hotel", None, None],
        ]
    }
    assert first["markup"] == msg.markup_json()
    assert second["markup"] is None
