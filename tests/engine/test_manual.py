from datetime import UTC, datetime

from app.engine.bus import Delivery
from app.engine.commands import CommandClass
from app.engine.gateway.types import ActionKind, Source, Verdict
from app.engine.manual import any_reply, click_request, fingerprint, send_request
from app.engine.parsing.refusals import Busy, Refused
from tests.engine.helpers import GAME, make_msg

AT = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _delivery(*events: object) -> Delivery:
    return Delivery(make_msg("x"), tuple(events), 0, 1)  # type: ignore[arg-type]


def test_any_reply_confirms_and_refuses() -> None:
    assert any_reply(_delivery()) is not None
    ok = any_reply(_delivery())
    assert ok is not None and (ok.verdict, ok.detail) == (Verdict.CONFIRMED, "reply")
    busy = any_reply(_delivery(Busy(left_s=5)))
    assert busy is not None and (busy.verdict, busy.detail) == (Verdict.REFUSED, "busy")
    no = any_reply(_delivery(Refused(reason="no_motivation")))
    assert no is not None and (no.verdict, no.detail) == (Verdict.REFUSED, "no_motivation")


def test_manual_requests() -> None:
    nav = send_request("😎Я", chat_id=GAME, cls=CommandClass.NAV, key="a", confirm=None)
    assert nav.source is Source.MANUAL and nav.expect is None
    assert nav.idempotency_key == "manual:a"
    act = send_request("/job", chat_id=GAME, cls=CommandClass.ACTION, key=None, confirm=None)
    assert act.expect is not None and act.idempotency_key is None
    click = click_request(
        chat_id=GAME,
        message_id=5,
        revision=7,
        data="gorbushka_fight",
        cls=CommandClass.ACTION,
        key="b",
        confirm=(3, AT),
    )
    assert click.kind is ActionKind.CLICK and click.expect_revision == 7
    assert click.risky_confirmed and click.expect is not None
    assert (click.confirm_version, click.confirm_until) == (3, AT)
    assert fingerprint(click) != fingerprint(nav)
    assert fingerprint(nav) == fingerprint(
        send_request("😎Я", chat_id=GAME, cls=CommandClass.NAV, key="z", confirm=None)
    )
