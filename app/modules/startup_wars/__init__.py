import logging

from config import (
    FABRICA_HOUR,
    FABRICA_MINUTE,
    FOOD_CONTROL_HOURS,
    FOOD_CONTROL_MINUTE,
    GORBUSHKA_HOURS,
    GORBUSHKA_MINUTE,
    PP_CONTROL_HOURS,
    PP_CONTROL_MINUTE,
    TANGERINE_HOUR,
    TANGERINE_MINUTE,
)
from core.module import BaseModule

from modules.startup_wars import commands, gorbushka, misc
from modules.startup_wars.daily import DailyQuests
from modules.startup_wars.metro import MetroGame
from modules.startup_wars.smoothie import SmoothieCrafter

logger = logging.getLogger(__name__)


class StartupWarsModule(BaseModule):
    name = "startup_wars"

    def __init__(self, app, scheduler):
        super().__init__(app, scheduler)
        self.daily = DailyQuests()
        self.metro = MetroGame()
        self.smoothie = SmoothieCrafter()

    def register_handlers(self) -> None:
        self.daily.register_handlers(self.app)
        self.metro.register_handlers(self.app)
        self.smoothie.register_handlers(self.app)
        gorbushka.register_handlers(self.app)
        misc.register_handlers(self.app)
        commands.register_handlers(self.app)

    def register_jobs(self) -> None:
        self.scheduler.add_job(
            gorbushka.food_control,
            "cron",
            minute=FOOD_CONTROL_MINUTE,
            hour=FOOD_CONTROL_HOURS,
            args=[self.app],
        )
        self.scheduler.add_job(
            gorbushka.gorbushka_control,
            "cron",
            minute=GORBUSHKA_MINUTE,
            hour=GORBUSHKA_HOURS,
            args=[self.app],
        )
        self.scheduler.add_job(
            misc.PP_control,
            "cron",
            minute=PP_CONTROL_MINUTE,
            hour=PP_CONTROL_HOURS,
            args=[self.app],
        )
        self.scheduler.add_job(
            misc.fabrica_control,
            "cron",
            minute=FABRICA_MINUTE,
            hour=FABRICA_HOUR,
            args=[self.app],
        )
        self.scheduler.add_job(
            misc.give_tangerine,
            "cron",
            minute=TANGERINE_MINUTE,
            hour=TANGERINE_HOUR,
            args=[self.app],
        )
