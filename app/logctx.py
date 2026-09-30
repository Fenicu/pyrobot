"""Аккаунт в каждой строке лога: движок аккаунта создаёт свои задачи (и задачи клиента kurigram
в их контексте) при выставленном `current_account`, фильтр переносит его в запись."""

import logging
from contextvars import ContextVar

current_account: ContextVar[int | None] = ContextVar("current_account", default=None)


class AccountLogFilter(logging.Filter):
    """Ставит `record.account` — аккаунт контекста записи, вне аккаунта — `-`. Вешается на
    обработчик: фильтры логгера не видят записей его потомков."""

    def filter(self, record: logging.LogRecord) -> bool:
        account = current_account.get()
        record.account = "-" if account is None else account
        return True
