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
    # Ключ шифрования сессий Telegram (32 байта, urlsafe base64): разбирает и сверяет с базой
    # `app.db.crypto` при старте процесса; `secret_key_reset` — осознанный сброс сессий при
    # другом ключе.
    secret_key: SecretStr | None = None
    secret_key_reset: bool = False
    http_host: str = "0.0.0.0"
    http_port: int = 8080
    # Адрес(а) обратного прокси (Caddy), которому uvicorn доверяет X-Forwarded-For/-Proto:
    # иначе лимитер входа видит всех клиентов одним адресом прокси; gateway — шлюз сети контейнера.
    forwarded_allow_ips: str = "127.0.0.1"
    cookie_secure: bool = True
    transport: Literal["kurigram", "fake"] = "kurigram"
    log_level: str = "INFO"
    planner: bool = True
    # Пул соединений базы на процесс; ёмкость хоста движков и пауза между стартами движков.
    db_pool_size: int = 10
    db_max_overflow: int = 20
    max_engines: int = 20
    engine_start_gap_s: float = 3.0
    # Запросов кода входа Telegram в час на хост.
    tg_codes_per_hour: int = 10
    # Собранная админка (SvelteKit, `admin/build`): её отдаёт то же приложение; каталога нет —
    # `/` отвечает 404 (разработка, тесты).
    admin_dir: Path | None = Path("/app/admin")

    @model_validator(mode="after")
    def _kurigram_credentials(self) -> Self:
        if self.transport == "kurigram":
            if self.tg_api_id <= 0:
                raise ValueError("tg_api_id must be set for kurigram transport")
            if not self.tg_api_hash.get_secret_value():
                raise ValueError("tg_api_hash must be set for kurigram transport")
        return self
