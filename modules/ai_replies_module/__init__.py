from logging import getLogger

from aiogram.types import BotCommand
from core.modules import Module
from playerokapi.enums import EventTypes

from .meta import *
from .module_logging import configure_logging
from .plbot.handlers import on_new_message
from .settings import Settings as ModuleSettings
from .tgbot import router


configure_logging()
logger = getLogger(NAME)
_module: Module = None


def set_module(new: Module):
    global _module
    _module = new


def get_module():
    return _module


async def on_module_enabled(module: Module):
    set_module(module)
    logger.info(f"{PREFIX} Модуль подключен и активен")
    try:
        config = ModuleSettings.get("config")
        rules = config.get("rules", {})
        logger.info(
            "%s Конфигурация: enabled=%s, модель=%s, групп=%s, общих пунктов FAQ=%s, ключ=%s",
            PREFIX,
            bool(rules.get("enabled")),
            config.get("ai", {}).get("model", "—"),
            len(config.get("groups", []) or []),
            len(config.get("knowledge", []) or []),
            "задан" if config.get("ai", {}).get("api_key") else "не задан",
        )
    except Exception:
        logger.exception("%s Не удалось прочитать конфигурацию при включении", PREFIX)


async def on_telegram_bot_init(telegram_bot):
    try:
        commands = await telegram_bot.bot.get_my_commands()
        existing = {command.command for command in commands}
        changed = False
        for command, description in (
            (NAME, "Настройки ИИ-автоответов"),
        ):
            if command not in existing:
                commands.append(BotCommand(command=command, description=description))
                changed = True
        if changed:
            await telegram_bot.bot.set_my_commands(commands)
    except Exception:
        logger.exception("Не удалось добавить команду ИИ-автоответов в список Telegram")


BOT_EVENT_HANDLERS = {
    "ON_MODULE_ENABLED": [on_module_enabled],
    "ON_TELEGRAM_BOT_INIT": [on_telegram_bot_init]
}

PLAYEROK_EVENT_HANDLERS = {
    EventTypes.NEW_MESSAGE: [on_new_message]
}

TELEGRAM_BOT_ROUTERS = [router]
