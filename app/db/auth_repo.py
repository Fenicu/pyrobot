from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update

from app.api.security import hash_password, new_token, token_hash
from app.db.base import Database
from app.db.models import AdminUser, AuthSession


@dataclass(frozen=True)
class Resolved:
    session: AuthSession
    admin: AdminUser
    slid: bool


class AuthRepo:
    def __init__(
        self,
        db: Database,
        *,
        ttl: timedelta = timedelta(days=30),
        slide_after: timedelta = timedelta(days=1),
    ) -> None:
        self._db = db
        self._ttl = ttl
        self._slide_after = slide_after

    @property
    def ttl(self) -> timedelta:
        return self._ttl

    async def ensure_admin(self, login: str, password: str | None) -> None:
        if await self.get_admin(login) is not None or not password:
            return
        password_hash = await hash_password(password)
        async with self._db.sessions() as session, session.begin():
            session.add(AdminUser(login=login, password_hash=password_hash))

    async def get_admin(self, login: str) -> AdminUser | None:
        async with self._db.sessions() as session:
            return await session.scalar(select(AdminUser).where(AdminUser.login == login))

    async def create_session(
        self, admin_id: int, ip: str | None, ua: str | None
    ) -> tuple[str, AuthSession]:
        token = new_token()
        row = AuthSession(
            token_hash=token_hash(token),
            csrf_token=new_token(),
            admin_user_id=admin_id,
            expires_at=datetime.now(UTC) + self._ttl,
            ip=ip,
            user_agent=(ua or "")[:256] or None,
        )
        async with self._db.sessions() as session, session.begin():
            session.add(row)
        return token, row

    async def resolve(self, token: str, *, slide: bool = True) -> Resolved | None:
        """Живая сессия по токену. `slide=False` — только проверка, без продления: её делает
        открытый поток SSE, который иначе продлевал бы сессию без действий пользователя."""
        now = datetime.now(UTC)
        async with self._db.sessions() as session, session.begin():
            row = await session.scalar(
                select(AuthSession).where(
                    AuthSession.token_hash == token_hash(token), AuthSession.expires_at > now
                )
            )
            if row is None:
                return None
            admin = await session.get(AdminUser, row.admin_user_id)
            if admin is None:
                return None
            slid = slide and now - row.last_seen_at > self._slide_after
            if slid:
                row.last_seen_at = now
                row.expires_at = now + self._ttl
            return Resolved(row, admin, slid)

    async def purge_expired(self) -> int:
        async with self._db.sessions() as session, session.begin():
            deleted = await session.scalars(
                delete(AuthSession)
                .where(AuthSession.expires_at <= datetime.now(UTC))
                .returning(AuthSession.id)
            )
            return len(list(deleted))

    async def revoke(self, session_id: int) -> None:
        async with self._db.sessions() as session, session.begin():
            await session.execute(delete(AuthSession).where(AuthSession.id == session_id))

    async def change_password(self, admin_id: int, new_hash: str) -> None:
        async with self._db.sessions() as session, session.begin():
            await session.execute(
                update(AdminUser)
                .where(AdminUser.id == admin_id)
                .values(password_hash=new_hash, password_changed_at=datetime.now(UTC))
            )
            await session.execute(delete(AuthSession).where(AuthSession.admin_user_id == admin_id))
