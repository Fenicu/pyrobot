"""Служебные команды учёток админки — для root на сервере, в обход входа через API.

    docker compose exec pyrobot python -m app.tools.users set-password <login>

`set-password` спрашивает новый пароль дважды (без эха), записывает новый хэш и закрывает все
сессии этой учётки: войти ею можно только с новым паролем. Так возвращается доступ, когда пароль
забыт; править `admin_users` в базе вручную не нужно."""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

from app.api.security import PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, hash_password
from app.config import DbConfig
from app.db.auth_repo import AuthRepo
from app.db.base import Database


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


async def set_password(db: Database, login: str) -> int:
    """Код выхода: 0 — пароль изменён; 1 — нет такого логина, пароли не совпали или не годятся
    (причина — строкой в stderr, база не тронута)."""
    repo = AuthRepo(db)
    admin = await repo.get_admin(login)
    if admin is None:
        return _fail(f"учётки {login!r} нет")
    password = getpass.getpass(f"Новый пароль для {login}: ")
    if password != getpass.getpass("Повторите пароль: "):
        return _fail("пароли не совпадают")
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        return _fail(f"пароль: от {PASSWORD_MIN_LENGTH} до {PASSWORD_MAX_LENGTH} символов")
    await repo.change_password(admin.id, await hash_password(password))
    print(f"Пароль учётки {login!r} изменён, её сессии закрыты")
    return 0


async def _run(args: argparse.Namespace) -> int:
    db = Database(DbConfig().database_url)
    try:
        return await set_password(db, args.login)
    finally:
        await db.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.users", description="Служебные команды учёток админки."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("set-password", help="сменить пароль учётки админки")
    command.add_argument("login")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
