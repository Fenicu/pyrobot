"""Служебные команды учёток админки — для root на сервере, в обход входа через API.

    docker compose exec pyrobot python -m app.tools.users set-password <login>
    docker compose exec pyrobot python -m app.tools.users promote <login>

`set-password` спрашивает новый пароль дважды (без эха), записывает новый хэш и закрывает все
сессии этой учётки: войти ею можно только с новым паролем. Так возвращается доступ, когда пароль
забыт; править `users` в базе вручную не нужно.

`promote` назначает учётку владельцем сервера."""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

from app.api.security import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, hash_password
from app.config import DbConfig
from app.db.accounts import AccountRepo
from app.db.audit import CLI_ACTOR, AuditLog
from app.db.auth_repo import AuthRepo
from app.db.base import Database
from app.db.notifications import DbNotifier
from app.db.users import UserRepo


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


async def set_password(db: Database, login: str) -> int:
    """Код выхода: 0 — пароль изменён; 1 — нет такого логина, пароли не совпали или не годятся
    (причина — строкой в stderr, база не тронута)."""
    repo = AuthRepo(db)
    user = await repo.get_user(login)
    if user is None:
        return _fail(f"учётки {login!r} нет")
    password = getpass.getpass(f"Новый пароль для {login}: ")
    if password != getpass.getpass("Повторите пароль: "):
        return _fail("пароли не совпадают")
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        return _fail(f"пароль: от {PASSWORD_MIN_LENGTH} до {PASSWORD_MAX_LENGTH} символов")
    await repo.change_password(user.id, await hash_password(password))
    audit = AuditLog(db)
    await audit.write(
        CLI_ACTOR,
        "password_set_by_cli",
        target_type="user",
        target_id=user.id,
    )
    accounts = await AccountRepo(db).owned(user.id)
    for acc in accounts:
        await DbNotifier(db, acc.id).notify(
            "warn", "password_set_by_cli", "Пароль учётной записи изменён через командную строку"
        )
    print(f"Пароль учётки {login!r} изменён, её сессии закрыты")
    return 0


async def promote(db: Database, login: str) -> int:
    """Код выхода: 0 — учётка назначена владельцем; 1 — логин не найден."""
    repo = UserRepo(db)
    user = await repo.by_login(login)
    if user is None:
        return _fail(f"учётки {login!r} нет")
    try:
        await repo.promote(login)
    except KeyError:
        return _fail(f"учётки {login!r} нет")
    audit = AuditLog(db)
    await audit.write(
        CLI_ACTOR,
        "owner_promoted",
        target_type="user",
        target_id=user.id,
    )
    print(f"Учётка {login!r} теперь владелец сервера")
    return 0


async def _run(args: argparse.Namespace) -> int:
    db = Database(DbConfig().database_url)
    try:
        if args.command == "set-password":
            return await set_password(db, args.login)
        if args.command == "promote":
            return await promote(db, args.login)
        return _fail(f"неизвестная команда {args.command!r}")
    finally:
        await db.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.users", description="Служебные команды учёток админки."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    sp_cmd = commands.add_parser("set-password", help="сменить пароль учётки админки")
    sp_cmd.add_argument("login")
    promote_cmd = commands.add_parser("promote", help="сделать учётку владельцем сервера")
    promote_cmd.add_argument("login")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
