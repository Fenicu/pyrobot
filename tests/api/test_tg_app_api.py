import re

import pytest
from sqlalchemy import update

from app.api.app import create_api
from app.db.models import Account, TgSession
from tests.api.conftest import A1, Api, run_engine
from tests.engine.test_facade import build

pytestmark = pytest.mark.db
HASH = "A" * 32
APP = f"{A1}/tg/app"


async def test_put_and_delete_only_when_logged_out(api: Api) -> None:
    r = await api.client.put(APP, headers=api.headers, json={"api_id": 12345, "api_hash": HASH})
    assert r.status_code == 204, r.text
    assert api.container.accounts is not None
    stored = await api.container.accounts.tg_app(1)
    assert stored is not None and stored[0] == 12345
    assert api.container.box is not None
    assert api.container.box.open(stored[1], "tg_api_hash", 1) == HASH.lower().encode()
    async with api.db.sessions() as session, session.begin():
        session.add(TgSession(account_id=1, dc_id=2, date=1, user_id=42))
    assert await api.container.accounts.tg_logged_in(1)
    for method in ("put", "delete"):
        kwargs = {"json": {"api_id": 23456, "api_hash": HASH}} if method == "put" else {}
        r = await getattr(api.client, method)(APP, headers=api.headers, **kwargs)
        assert r.status_code == 409 and r.json()["detail"] == "tg_logged_in"
    async with api.db.sessions() as session, session.begin():
        await session.execute(update(TgSession).values(user_id=None))
    assert not await api.container.accounts.tg_logged_in(1)
    r = await api.client.delete(APP, headers=api.headers)
    assert r.status_code == 204 and await api.container.accounts.tg_app(1) is None


async def test_validation(api: Api) -> None:
    for app_id, app_hash in ((0, HASH), (2**31, HASH), (1, "a" * 31), (1, "g" * 32)):
        r = await api.client.put(
            APP, headers=api.headers, json={"api_id": app_id, "api_hash": app_hash}
        )
        assert r.status_code == 422
        assert app_hash not in r.text


async def test_hash_never_in_any_response(api: Api) -> None:
    r = await api.client.put(APP, headers=api.headers, json={"api_id": 12345, "api_hash": HASH})
    assert r.status_code == 204
    paths = create_api(api.container).openapi()["paths"]
    account_gets = [
        re.sub(r"\{[^}]+\}", "1", path)
        for path, methods in paths.items()
        if path.startswith("/api/v1/accounts/{account_id}/")
        and "get" in methods
        and not path.endswith("/events")
    ]
    for path in ["/api/v1/accounts", *account_gets]:
        response = await api.client.get(path)
        assert HASH not in response.text and HASH.lower() not in response.text


async def test_status_reports_app(api: Api) -> None:
    assert (await api.client.get(f"{A1}/tg/status")).json()["app"] == "server"
    await api.client.put(APP, headers=api.headers, json={"api_id": 12345, "api_hash": HASH})
    assert (await api.client.get(f"{A1}/tg/status")).json()["app"] == {"api_id": 12345}


async def test_put_reloads_registered_engine_and_drops_attempt(api: Api) -> None:
    engine = run_engine(api.container, build(authorized=False))
    await engine.facade.tg.boot()
    first = await engine.facade.tg.start("+888", owner="s1")
    assert first.attempt_id
    r = await api.client.put(APP, headers=api.headers, json={"api_id": 12345, "api_hash": HASH})
    assert r.status_code == 204
    assert engine.app_reloads == 1
    status = (await api.client.get(f"{A1}/tg/status")).json()
    assert status["state"] == "unauthorized" and status["attempt_id"] is None
    assert status["app"] == {"api_id": 12345}


async def test_deleting_rejected(api: Api) -> None:
    async with api.db.sessions() as session, session.begin():
        await session.execute(update(Account).where(Account.id == 1).values(status="deleting"))
    r = await api.client.put(APP, headers=api.headers, json={"api_id": 12345, "api_hash": HASH})
    assert r.status_code == 409 and r.json()["detail"] == "account_deleting"
