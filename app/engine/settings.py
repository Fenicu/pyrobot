from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class EngineSection(BaseModel):
    mode: Literal["dry_run", "live"] = "dry_run"
    killed: bool = False
    kill_reason: str | None = None
    min_request_interval_s: float = Field(default=1.6, ge=0)
    antiflood_retry_max: int = Field(default=2, ge=0)
    antiflood_pause_s: float = Field(default=10.0, ge=0)
    action_ttl_s: float = Field(default=60.0, gt=0)
    default_expect_timeout_s: float = Field(default=20.0, gt=0)
    click_answer_timeout_s: float = Field(default=4.0, gt=0)
    recovered_react_max_age_min: int = Field(default=10, ge=0)
    urgent_while_paused: bool = True
    manual_while_paused: bool = True


class TelegramSection(BaseModel):
    expected_user_id: int = 267519921


class ChatsSection(BaseModel):
    game_chat_id: int = 227859379
    swinfo_chat_id: int = -1001109615116
    swinfo_user_id: int = 376592453
    smoothie_channel_id: int = -1001356300612
    tangerine_chat_id: int = -1001377961602
    tangerine_reply_to: int = 927136
    bulls_invite_chat_id: int | None = None


class Settings(BaseModel):
    engine: EngineSection = Field(default_factory=EngineSection)
    telegram: TelegramSection = Field(default_factory=TelegramSection)
    chats: ChatsSection = Field(default_factory=ChatsSection)


class SettingsConflict(Exception):
    pass


SettingsChange = Callable[[Settings], Settings]


class SettingsProvider(Protocol):
    @property
    def current(self) -> Settings: ...

    @property
    def version(self) -> int: ...

    async def update(
        self, change: SettingsChange, *, changed_by: str, expected_version: int | None = None
    ) -> Settings: ...


class StaticSettings:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        self._version = 0

    @property
    def current(self) -> Settings:
        return self._settings

    @property
    def version(self) -> int:
        return self._version

    async def update(
        self, change: SettingsChange, *, changed_by: str, expected_version: int | None = None
    ) -> Settings:
        if expected_version is not None and expected_version != self._version:
            raise SettingsConflict(f"version {self._version} != {expected_version}")
        self._settings = Settings.model_validate(change(self._settings).model_dump())
        self._version += 1
        return self._settings
