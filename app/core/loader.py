import importlib
import inspect
import logging
import pkgutil

import modules
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pyrogram import Client

from core.module import BaseModule

logger = logging.getLogger(__name__)


def _find_module_classes(package) -> list[type[BaseModule]]:
    classes: list[type[BaseModule]] = []
    for importer, modname, ispkg in pkgutil.walk_packages(
        package.__path__, prefix=package.__name__ + "."
    ):
        mod = importlib.import_module(modname)
        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, BaseModule) and obj is not BaseModule:
                classes.append(obj)
    return classes


def load_modules(
    app: Client,
    scheduler: AsyncIOScheduler,
    enabled: list[str],
) -> list[BaseModule]:
    all_classes = _find_module_classes(modules)
    loaded: list[BaseModule] = []

    for cls in all_classes:
        if cls.name in enabled:
            instance = cls(app, scheduler)
            instance.load()
            loaded.append(instance)
            logger.info("Loaded module: %s", cls.name)
        else:
            logger.debug("Skipping disabled module: %s", cls.name)

    found_names = {cls.name for cls in all_classes}
    for name in enabled:
        if name not in found_names:
            logger.warning("Module '%s' is enabled but was not found", name)

    return loaded
