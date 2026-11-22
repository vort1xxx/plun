_registered = False


def get_router():
    from .menu import router
    return router


def register():
    global _registered
    if _registered:
        return
    from core.handlers import add_bot_event_handler
    from .module_logging import configure_logging
    configure_logging()
    add_bot_event_handler('ON_PLAYEROK_BOT_INIT', on_playerok_init)
    add_bot_event_handler('ON_TELEGRAM_BOT_INIT', on_telegram_init)
    _registered = True


async def on_playerok_init(bot):
    from .worker import ACTIVE, start
    ACTIVE.set()
    start(bot)


async def on_telegram_init(bot):
    import logging
    from aiogram.types import BotCommand
    try:
        commands = await bot.bot.get_my_commands()
        if not any(command.command == 'bumpgroups' for command in commands):
            commands.append(BotCommand(command='bumpgroups', description='Группы автоподнятия товаров'))
            await bot.bot.set_my_commands(commands)
    except Exception:
        logging.getLogger('bumpgroups').exception('Не удалось зарегистрировать команду групп поднятия')
