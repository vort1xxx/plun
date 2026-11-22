import ast
import logging
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / '__init__.py'


def functions():
    ns = {'__package__': 'report_test', '_registered': False}
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))], type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_commands_keep_existing_and_schedulers_start(self):
        profit, deals = AsyncMock(), Mock()
        bot = NS(get_my_commands=AsyncMock(return_value=[NS(command='start'), NS(command='profit')]),
                 set_my_commands=AsyncMock())
        modules = {'aiogram.types': NS(BotCommand=lambda **kw: NS(**kw)),
                   'report_test.profit.scheduler': NS(start=profit),
                   'report_test.deals.daily_scheduler': NS(start=deals)}
        with patch.dict('sys.modules', modules):
            await functions()['on_telegram_init'](NS(bot=bot))
        names = [cmd.command for cmd in bot.set_my_commands.call_args.args[0]]
        self.assertIn('start', names)
        self.assertIn('reports', names)
        self.assertEqual(names.count('profit'), 1)
        profit.assert_awaited_once()
        deals.assert_called_once()

    async def test_command_registration_failure_still_starts_schedulers(self):
        profit, deals = AsyncMock(), Mock()
        bot = NS(get_my_commands=AsyncMock(side_effect=RuntimeError('offline')))
        modules = {'aiogram.types': NS(BotCommand=Mock()),
                   'report_test.profit.scheduler': NS(start=profit),
                   'report_test.deals.daily_scheduler': NS(start=deals)}
        with patch.dict('sys.modules', modules), self.assertLogs('reports', logging.ERROR):
            await functions()['on_telegram_init'](NS(bot=bot))
        profit.assert_awaited_once()
        deals.assert_called_once()

    def test_event_handlers_registered_once(self):
        bot, playerok, handler = Mock(), Mock(), Mock()
        modules = {'core.handlers': NS(add_bot_event_handler=bot, add_playerok_event_handler=playerok),
                   'playerokapi.enums': NS(EventTypes=NS(NEW_DEAL='new_deal')),
                   'report_test.deals.plbot.handlers': NS(on_new_deal=handler)}
        ns = functions()
        with patch.dict('sys.modules', modules):
            ns['register']()
            ns['register']()
        playerok.assert_called_once_with('new_deal', handler)
        bot.assert_called_once_with('ON_TELEGRAM_BOT_INIT', ns['on_telegram_init'])


if __name__ == '__main__':
    unittest.main()
