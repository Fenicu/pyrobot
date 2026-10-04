import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update

from app.api.security import hash_password, new_token, token_hash
from app.db.base import Database
from app.db.models import AuthSession, User

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Resolved:
    session: AuthSession
    user: User
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

    async def ensure_owner(self, login: str, password: str | None) -> None:
        async with self._db.sessions() as session, session.begin():
            owners_count = await session.scalar(
                select(func.count()).select_from(User).where(User.role == "owner")
            )
            if owners_count and owners_count > 0:
                return
            user = await session.scalar(select(User).where(User.login == login))
            if user is not None:
                log.warning(
                    "no owner: user %r exists with role user; promote it with app.tools.users",
                    login,
                )
                return
            if not password:
                return
            password_hash = await hash_password(password)
            session.add(
                User(login=login, password_hash=password_hash, role="owner", max_accounts=10)
            )

    async def get_user(self, login: str) -> User | None:
        async with self._db.sessions() as session:
            return await session.scalar(select(User).where(User.login == login))

    async def create_session(
        self, user_id: int, ip: str | None, ua: str | None
    ) -> tuple[str, AuthSession]:
        token = new_token()
        row = AuthSession(
            token_hash=token_hash(token),
            csrf_token=new_token(),
            admin_user_id=user_id,
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
            user = await session.get(User, row.admin_user_id)
            if user is None:
                return None
            if user.disabled_at is not None or user.deleting_at is not None:
                return None
            slid = slide and now - row.last_seen_at > self._slide_after
            if slid:
                row.last_seen_at = now
                row.expires_at = now + self._ttl
            return Resolved(row, user, slid)

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

    async def revoke_all(self, user_id: int) -> int:
        async with self._db.sessions() as session, session.begin():
            deleted = await session.scalars(
                delete(AuthSession)
                .where(AuthSession.admin_user_id == user_id)
                .returning(AuthSession.id)
            )
            return len(list(deleted))

    async def change_password(self, user_id: int, new_hash: str) -> None:
        async with self._db.sessions() as session, session.begin():
            await session.execute(
                update(User)
                .where(User.id == user_id)
                .values(password_hash=new_hash, password_changed_at=datetime.now(UTC))
            )
            await session.execute(delete(AuthSession).where(AuthSession.admin_user_id == user_id))
