from abc import ABC, abstractmethod

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pyrogram import Client


class BaseModule(ABC):
    name: str

    def __init__(self, app: Client, scheduler: AsyncIOScheduler):
        self.app = app
        self.scheduler = scheduler

    @abstractmethod
    def register_handlers(self) -> None: ...

    def register_jobs(self) -> None:
        pass

    def load(self) -> None:
        self.register_handlers()
        self.register_jobs()
