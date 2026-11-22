import logging

from aiogram.types import BotCommand
from playerokapi.enums import EventTypes

from .meta import *
from .module_logging import configure_logging
from .plbot.handlers import on_new_deal, on_new_message, on_rollback
from .storage import get_config
from .tgbot import router

configure_logging()
logger = logging.getLogger(NAME)


async def on_module_enabled(module):
    config = get_config()
    logger.info("Модуль включён: привязок=%s", len(config["mappings"]))


async def on_telegram_bot_init(bot):
    try:
        commands = await bot.bot.get_my_commands()
        if not any(command.command == NAME for command in commands):
            commands.append(BotCommand(command=NAME, description="Настройки выдачи 2FA"))
            await bot.bot.set_my_commands(commands)
    except Exception as exc:
        logger.error("Ошибка регистрации команды: error=%s", type(exc).__name__)


BOT_EVENT_HANDLERS = {"ON_MODULE_ENABLED": [on_module_enabled], "ON_TELEGRAM_BOT_INIT": [on_telegram_bot_init]}
PLAYEROK_EVENT_HANDLERS = {EventTypes.NEW_DEAL: [on_new_deal], EventTypes.NEW_MESSAGE: [on_new_message], EventTypes.DEAL_ROLLED_BACK: [on_rollback]}
TELEGRAM_BOT_ROUTERS = [router]
