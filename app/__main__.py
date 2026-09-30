import logging
import socket
import struct
from pathlib import Path
from typing import Any

import uvicorn

from app.config import AppConfig
from app.logctx import AccountLogFilter
from app.main import create_application

log = logging.getLogger(__name__)
ROUTE_TABLE = Path("/proc/net/route")
# В списке доверенных прокси — шлюз сети контейнера: с его адреса приходят запросы на порт,
# опубликованный на хосте (прокси на этом же сервере), а не с 127.0.0.1.
GATEWAY = "gateway"
# `account` ставит AccountLogFilter на обработчике: аккаунт задачи или `-`.
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s account=%(account)s %(message)s"


# Флаги маршрута (linux/route.h): маршрут поднят и идёт через шлюз.
RTF_UP, RTF_GATEWAY = 0x1, 0x2


def _default_gateway(table: Path) -> str | None:
    """Шлюз маршрута по умолчанию (назначение и маска 0, флаги UP и GATEWAY; из нескольких — с
    меньшей метрикой). Таблицы нет — None; таблица не разбирается — None и запись в лог."""
    try:
        lines = table.read_text(encoding="utf-8").splitlines()[1:]
    except OSError:
        return None
    best: tuple[int, int] | None = None
    try:
        for line in lines:
            _, dest, gateway, flags, _, _, metric, mask = line.split()[:8]
            wanted = (int(flags, 16) & (RTF_UP | RTF_GATEWAY)) == (RTF_UP | RTF_GATEWAY)
            if int(dest, 16) == 0 and int(mask, 16) == 0 and wanted:
                candidate = (int(metric), int(gateway, 16))
                best = candidate if best is None or candidate < best else best
        return None if best is None else socket.inet_ntoa(struct.pack("<L", best[1]))
    except (ValueError, struct.error) as exc:
        log.warning("route table %s not parsed: %s", table, exc)
        return None


def trusted_proxies(value: str, table: Path | None = None) -> str:
    """Адреса, чьему X-Forwarded-For верить; `gateway` — шлюз маршрута по умолчанию контейнера.
    Шлюз не найден — он не доверяется; не осталось никого — только 127.0.0.1 (сам контейнер)."""
    out: list[str] = []
    for item in (part.strip() for part in value.split(",")):
        if item != GATEWAY:
            out.append(item)
        elif (gateway := _default_gateway(table or ROUTE_TABLE)) is not None:
            out.append(gateway)
        else:
            log.warning("no default gateway: forwarded headers from it are not trusted")
    return ",".join(item for item in out if item) or "127.0.0.1"


def pyrogram_log_level(level: str) -> int:
    # DEBUG kurigram печатает код входа в Telegram.
    return max(logging.INFO, logging.getLevelNamesMapping()[level.upper()])


def uvicorn_options(cfg: AppConfig) -> dict[str, Any]:
    return {
        "host": cfg.http_host,
        "port": cfg.http_port,
        "workers": 1,
        # Стандартный asyncio и h11 без WebSocket: uvloop и httptools не ставятся (uvicorn без
        # [standard]), и выбор не зависит от того, что оказалось в окружении.
        "loop": "asyncio",
        "http": "h11",
        "ws": "none",
        "log_level": cfg.log_level.lower(),
        "proxy_headers": True,
        "forwarded_allow_ips": trusted_proxies(cfg.forwarded_allow_ips),
        # Открытый SSE иначе держит SIGTERM до SIGKILL, и Runtime.stop не выполняется.
        "timeout_graceful_shutdown": 5,
    }


def main() -> None:
    cfg = AppConfig()
    logging.basicConfig(level=cfg.log_level.upper(), format=LOG_FORMAT)
    for handler in logging.getLogger().handlers:
        handler.addFilter(AccountLogFilter())
    logging.getLogger("pyrogram").setLevel(pyrogram_log_level(cfg.log_level))
    options = uvicorn_options(cfg)
    log.info("trusted proxies: %s", options["forwarded_allow_ips"])
    uvicorn.run(create_application(cfg), **options)


if __name__ == "__main__":
    main()
