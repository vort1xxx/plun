_registered = False


def get_routers():
    from .menu import router
    from .profit.tgbot import router as profit_router
    from .deals.tgbot import router as deals_router
    return [router, profit_router, deals_router]


def register():
    global _registered
    if _registered:
        return
    from core.handlers import add_bot_event_handler, add_playerok_event_handler
    from playerokapi.enums import EventTypes
    from .deals.plbot.handlers import on_new_deal
    add_playerok_event_handler(EventTypes.NEW_DEAL, on_new_deal)
    add_bot_event_handler('ON_TELEGRAM_BOT_INIT', on_telegram_init)
    _registered = True


async def on_telegram_init(telegram):
    import logging
    from aiogram.types import BotCommand
    from .profit.scheduler import start as start_profit
    from .deals.daily_scheduler import start as start_deals
    try:
        commands = await telegram.bot.get_my_commands()
        existing = {command.command for command in commands}
        for name, title in (('reports', 'Отчёты и история продаж'), ('profit', 'Продажи за период'),
                            ('profitreport', 'Настройки отчётов по продажам'), ('deals', 'История сделок'),
                            ('deals_data', 'Сохранение и выгрузка истории'), ('deals_report', 'Отчёт за вчера')):
            if name not in existing:
                commands.append(BotCommand(command=name, description=title))
        await telegram.bot.set_my_commands(commands)
    except Exception:
        logging.getLogger('reports').exception('Не удалось зарегистрировать команды отчётов')
    finally:
        await start_profit(telegram)
        start_deals()
