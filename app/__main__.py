import logging

import uvicorn

from app.config import AppConfig
from app.main import create_application


def main() -> None:
    cfg = AppConfig()
    logging.basicConfig(
        level=cfg.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    uvicorn.run(
        create_application(cfg),
        host=cfg.http_host,
        port=cfg.http_port,
        workers=1,
        log_level=cfg.log_level.lower(),
    )


if __name__ == "__main__":
    main()
