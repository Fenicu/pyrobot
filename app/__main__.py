import logging

import uvicorn

from app.config import AppConfig
from app.main import create_application


def pyrogram_log_level(level: str) -> int:
    # DEBUG kurigram печатает код входа в Telegram.
    return max(logging.INFO, logging.getLevelNamesMapping()[level.upper()])


def main() -> None:
    cfg = AppConfig()
    logging.basicConfig(
        level=cfg.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("pyrogram").setLevel(pyrogram_log_level(cfg.log_level))
    uvicorn.run(
        create_application(cfg),
        host=cfg.http_host,
        port=cfg.http_port,
        workers=1,
        log_level=cfg.log_level.lower(),
    )


if __name__ == "__main__":
    main()
