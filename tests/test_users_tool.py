from collections.abc import Iterator

import pytest
from sqlalchemy import func, select

from app.api.security import hash_password, verify_password
from app.db.auth_repo import AuthRepo
from app.db.base import Database
from app.db.models import AuthSession, User
from app.db.users import UserRepo
from app.tools import users

pytestmark = pytest.mark.db

OLD = "old password 123"
NEW = "new password 4567"


def answers(monkeypatch: pytest.MonkeyPatch, *typed: str) -> list[str]:
    """Подменяет getpass: отвечает по очереди заготовленными строками; возвращает вопросы."""
    replies: Iterator[str] = iter(typed)
    asked: list[str] = []

    def fake_getpass(prompt: str = "") -> str:
        asked.append(prompt)
        return next(replies)

    monkeypatch.setattr(users.getpass, "getpass", fake_getpass)
    return asked


async def seed(db: Database, *logins: str) -> AuthRepo:
    repo = AuthRepo(db)
    password_hash = await hash_password(OLD)
    async with db.sessions() as s, s.begin():
        for i, login in enumerate(logins):
            s.add(
                User(
                    login=login,
                    password_hash=password_hash,
                    role="owner" if i == 0 else "user",
                    max_accounts=10,
                )
            )
    return repo


async def hash_of(repo: AuthRepo, login: str) -> str:
    user = await repo.get_user(login)
    assert user is not None
    return user.password_hash


async def test_set_password_changes_hash_and_closes_sessions(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = await seed(clean_db, "admin", "other")
    admin, other = await repo.get_user("admin"), await repo.get_user("other")
    assert admin is not None and other is not None
    token, _ = await repo.create_session(admin.id, None, None)
    await repo.create_session(admin.id, None, None)
    other_token, _ = await repo.create_session(other.id, None, None)
    other_hash = await hash_of(repo, "other")
    asked = answers(monkeypatch, NEW, NEW)

    assert await users.set_password(clean_db, "admin") == 0

    new_hash = await hash_of(repo, "admin")
    assert await verify_password(new_hash, NEW) and not await verify_password(new_hash, OLD)
    assert await repo.resolve(token) is None
    async with clean_db.sessions() as s:
        left = list(await s.scalars(select(AuthSession.admin_user_id)))
    assert left == [other.id]
    assert await repo.resolve(other_token) is not None
    assert await hash_of(repo, "other") == other_hash
    # Пароль не печатается: ни в вопросах, ни в выводе.
    out = capsys.readouterr()
    assert len(asked) == 2 and NEW not in out.out + out.err + "".join(asked)
    assert "admin" in out.out and out.err == ""


async def test_set_password_unknown_login_exit_1(
    clean_db: Database, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = await seed(clean_db, "admin")
    before = await hash_of(repo, "admin")
    asked = answers(monkeypatch, NEW, NEW)

    assert await users.set_password(clean_db, "nobody") == 1

    # Пароль не спрашивали: логин проверен до ввода.
    assert asked == []
    assert "nobody" in capsys.readouterr().err
    assert await hash_of(repo, "admin") == before


@pytest.mark.parametrize(
    ("typed", "fragment"),
    [
        ((NEW, NEW + "x"), "не совпадают"),
        (("short", "short"), "12"),
        (("x" * 1025, "x" * 1025), "1024"),
    ],
    ids=["mismatch", "short", "too-long"],
)
async def test_set_password_mismatch_or_short_exit_1(
    clean_db: Database,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    typed: tuple[str, str],
    fragment: str,
) -> None:
    repo = await seed(clean_db, "admin")
    admin = await repo.get_user("admin")
    assert admin is not None
    await repo.create_session(admin.id, None, None)
    before = await hash_of(repo, "admin")
    answers(monkeypatch, *typed)

    assert await users.set_password(clean_db, "admin") == 1

    assert fragment in capsys.readouterr().err
    assert await hash_of(repo, "admin") == before
    async with clean_db.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(AuthSession)) == 1


async def test_promote_makes_owner(clean_db: Database) -> None:
    async with clean_db.sessions() as s, s.begin():
        s.add(User(login="bob", password_hash="h", role="user"))
    assert await users.promote(clean_db, "bob") == 0
    u = await UserRepo(clean_db).by_login("bob")
    assert u is not None and u.role == "owner"


async def test_promote_unknown_login_exit_1(
    clean_db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await users.promote(clean_db, "nobody") == 1
    assert "nobody" in capsys.readouterr().err
