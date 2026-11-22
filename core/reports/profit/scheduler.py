import asyncio
from datetime import datetime, timedelta
import logging

from .profit import REPORT_TZ
from .settings import Settings

logger = logging.getLogger('reports.profit')
_task = None


async def start(telegram_bot):
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(run(telegram_bot))


async def run(telegram_bot):
    from .tgbot.handlers import send_daily_report
    while True:
        try:
            now = datetime.now(REPORT_TZ)
            config = Settings.get('config').get('playerok_profit_report', {})
            today = now.date().isoformat()
            if (config.get('daily_enabled', True)
                    and now.strftime('%H:%M') >= str(config.get('daily_time', '21:00'))
                    and config.get('daily_last_sent') != today):
                await send_daily_report(telegram_bot, now.date() - timedelta(days=1))
                # Re-read so edits made during report generation survive.
                latest = Settings.get('config')
                latest.setdefault('playerok_profit_report', {})['daily_last_sent'] = today
                Settings.set('config', latest)
        except Exception:
            logger.exception('Не удалось отправить ежедневный отчёт по продажам')
        await asyncio.sleep(15)
