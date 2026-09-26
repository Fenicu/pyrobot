from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PYROBOT_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://pyrobot:pyrobot@localhost:55432/pyrobot"
    data_dir: Path = Path("/data")
    tg_api_id: int = 0
    tg_api_hash: SecretStr = SecretStr("")
    admin_login: str = "admin"
    admin_password: SecretStr | None = None
    http_host: str = "0.0.0.0"
    http_port: int = 8080
    cookie_secure: bool = True
    transport: Literal["kurigram", "fake"] = "kurigram"
    log_level: str = "INFO"
    account_id: int = 1
