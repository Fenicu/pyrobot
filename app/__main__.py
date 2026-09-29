import logging
import socket
import struct
from pathlib import Path
from typing import Any

import uvicorn

from app.config import AppConfig
from app.main import create_application

log = logging.getLogger(__name__)
ROUTE_TABLE = Path("/proc/net/route")
# В списке доверенных прокси — шлюз сети контейнера: с его адреса приходят запросы на порт,
# опубликованный на хосте (прокси на этом же сервере), а не с 127.0.0.1.
GATEWAY = "gateway"


def _default_gateway(table: Path) -> str | None:
    try:
        lines = table.read_text().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        fields = line.split()
        if len(fields) > 2 and fields[1] == "00000000" and fields[2] != "00000000":
            return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
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
        "log_level": cfg.log_level.lower(),
        "proxy_headers": True,
        "forwarded_allow_ips": trusted_proxies(cfg.forwarded_allow_ips),
        # Открытый SSE иначе держит SIGTERM до SIGKILL, и Runtime.stop не выполняется.
        "timeout_graceful_shutdown": 5,
    }


def main() -> None:
    cfg = AppConfig()
    logging.basicConfig(
        level=cfg.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("pyrogram").setLevel(pyrogram_log_level(cfg.log_level))
    options = uvicorn_options(cfg)
    log.info("trusted proxies: %s", options["forwarded_allow_ips"])
    uvicorn.run(create_application(cfg), **options)


if __name__ == "__main__":
    main()
