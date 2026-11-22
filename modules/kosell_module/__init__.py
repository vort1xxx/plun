from logging import getLogger

from aiogram.types import BotCommand
from core.modules import Module
from playerokapi.enums import EventTypes

from .meta import *
from .module_logging import configure_logging
from .plbot.handlers import on_new_deal, on_new_message
from .settings import Settings
from .tgbot import router

configure_logging()
logger = getLogger(NAME)
_module: Module | None = None


def set_module(module: Module) -> None:
    global _module
    _module = module


def get_module() -> Module | None:
    return _module


async def on_module_enabled(module: Module) -> None:
    set_module(module)
    config = Settings.get("config").get("playerok", {})
    logger.info(
        "%s Модуль подключен и активен: API-ключ %s, связок товаров %d",
        PREFIX,
        "задан" if config.get("api_key") else "не задан",
        len(config.get("mappings", [])),
    )


async def on_telegram_bot_init(telegram_bot) -> None:
    """Добавляет команду модуля, сохраняя команды Playerok Universal."""
    try:
        commands = await telegram_bot.bot.get_my_commands()
        if not any(command.command == NAME for command in commands):
            commands.append(BotCommand(command=NAME, description="Автовыдача аренды KOSell"))
            await telegram_bot.bot.set_my_commands(commands)
    except Exception:
        logger.exception("Не удалось добавить /%s в список команд Telegram", NAME)


BOT_EVENT_HANDLERS = {
    "ON_MODULE_ENABLED": [on_module_enabled],
    "ON_TELEGRAM_BOT_INIT": [on_telegram_bot_init],
}
PLAYEROK_EVENT_HANDLERS = {
    EventTypes.NEW_DEAL: [on_new_deal],
    EventTypes.NEW_MESSAGE: [on_new_message],
}
TELEGRAM_BOT_ROUTERS = [router]
