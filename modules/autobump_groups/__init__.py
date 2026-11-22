import logging
from aiogram.types import BotCommand

from .meta import *
from .menu import router
from .module_logging import configure_logging
from .worker import on_bot_init, on_enabled, on_disabled

configure_logging()


async def on_telegram_init(bot):
    try:
        commands = await bot.bot.get_my_commands()
        if not any(command.command == NAME for command in commands):
            commands.append(BotCommand(command=NAME, description="Группы автоподнятия товаров"))
            await bot.bot.set_my_commands(commands)
    except Exception as exc:
        logging.getLogger(NAME).error("Ошибка регистрации команды: %s", type(exc).__name__)


BOT_EVENT_HANDLERS = {
    "ON_MODULE_ENABLED": [on_enabled], "ON_MODULE_DISABLED": [on_disabled],
    "ON_PLAYEROK_BOT_INIT": [on_bot_init], "ON_TELEGRAM_BOT_INIT": [on_telegram_init],
}
PLAYEROK_EVENT_HANDLERS = {}
TELEGRAM_BOT_ROUTERS = [router]
