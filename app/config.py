from pathlib import Path
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DbConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PYROBOT_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://pyrobot:pyrobot@localhost:55432/pyrobot"


class AppConfig(DbConfig):
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
    planner: bool = True

    @model_validator(mode="after")
    def _kurigram_credentials(self) -> Self:
        if self.transport == "kurigram":
            if self.tg_api_id <= 0:
                raise ValueError("tg_api_id must be set for kurigram transport")
            if not self.tg_api_hash.get_secret_value():
                raise ValueError("tg_api_hash must be set for kurigram transport")
        return self
