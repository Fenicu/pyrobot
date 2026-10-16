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


# Строки kurigram о повторе запроса или подключения после сбоя сети — по логгеру; при обрыве
# связи их тысячи. Сам обрыв пишет одной строкой проба связи (`Reachability`).
_NETWORK_RETRIES: dict[str, tuple[str, ...]] = {
    "pyrogram.session.session": ('[%s] Retrying "%s" due to: %s',),
    "pyrogram.connection.connection": (
        "Unable to connect due to network issues: %s",
        "Connection failed! Trying again...",
    ),
}


class NetworkRetryFilter(logging.Filter):
    """Повтор после сбоя сети — DEBUG, а не WARNING: запись получает уровень DEBUG и проходит,
    только если логгер его пишет. Остальные предупреждения и ошибки kurigram — как есть."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg not in _NETWORK_RETRIES.get(record.name, ()):
            return True
        record.levelno, record.levelname = logging.DEBUG, logging.getLevelName(logging.DEBUG)
        return logging.getLogger(record.name).isEnabledFor(logging.DEBUG)


def quiet_network_retries() -> list[tuple[logging.Logger, logging.Filter]]:
    """Фильтр повторов — на логгеры kurigram, которые их пишут."""
    installed: list[tuple[logging.Logger, logging.Filter]] = []
    for name in _NETWORK_RETRIES:
        logger, flt = logging.getLogger(name), NetworkRetryFilter()
        logger.addFilter(flt)
        installed.append((logger, flt))
    return installed
