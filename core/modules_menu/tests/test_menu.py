"""Offline integration checks with Telegram and module registry replaced."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def module(name, **values):
    result = ModuleType(name)
    result.__dict__.update(values)
    return result


class Command:
    def __init__(self, *commands):
        self.commands, self.prefix = commands, '/'


class Filter:
    def __getattr__(self, key): return self
    def startswith(self, value): return self


class Router:
    def __init__(self):
        self.message = self
        self.handlers = []
        self.sub_routers = []
        self.propagate_event = AsyncMock(return_value=None)
    def __call__(self, *filters):
        def register(function):
            self.handlers.append(NS(callback=function, filters=[NS(callback=f) for f in filters]))
            return function
        return register
    callback_query = __call__


class CallbackData:
    def __init__(self, **kwargs): self.values = kwargs
    def pack(self): return type(self).__name__ + ':' + ':'.join(str(v) for v in self.values.values())


class ModulesPagination(CallbackData): pass
class ModulePage(CallbackData): pass
class MenuNavigation(CallbackData): pass


class Message:
    def __init__(self, **kwargs): self.__dict__.update(kwargs)
    def model_copy(self, update): return Message(**(self.__dict__ | update))


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.registry = []
        self.settings = Mock()
        self.settings.get.return_value = {'telegram': {'bot': {'signed_users': [42]}}}
        self.old_keyboard = lambda: NS(inline_keyboard=[[NS(text='Modules', callback_data=ModulesPagination(page=0).pack()), NS(text='Other', callback_data='other')]])
        self.templates = module('tgbot.templates', menu_kb=self.old_keyboard)
        self.original_template = module('tgbot.templates.menu', menu_kb=self.old_keyboard)
        calls = NS(ModulesPagination=ModulesPagination, ModulePage=ModulePage, MenuNavigation=MenuNavigation)
        self.unhandled = object()
        self.auth = AsyncMock()
        overrides = {
            'modules_menu_offline': module('modules_menu_offline', __path__=[str(ROOT)]),
            'aiogram': module('aiogram', F=Filter(), Router=Router, types=NS(Message=Message, CallbackQuery=object)),
            'aiogram.filters': module('aiogram.filters', Command=Command),
            'aiogram.types': module('aiogram.types', InlineKeyboardButton=NS, InlineKeyboardMarkup=NS, MessageEntity=NS),
            'aiogram.dispatcher.event.bases': module('aiogram.dispatcher.event.bases', UNHANDLED=self.unhandled),
            'core.modules': module('core.modules', get_modules=lambda: self.registry,
                get_module_by_uuid=lambda uid: next((m for m in self.registry if m.uuid == uid), None)),
            'settings': module('settings', Settings=self.settings),
            'tgbot': module('tgbot', templates=self.templates, callback_datas=calls),
            'tgbot.templates': self.templates,
            'tgbot.templates.menu': self.original_template,
            'tgbot.helpful': module('tgbot.helpful', do_auth=self.auth),
        }
        override = patch.dict(sys.modules, overrides)
        override.start()
        self.addCleanup(override.stop)
        def load(name):
            spec = importlib.util.spec_from_file_location('modules_menu_offline.' + name, ROOT / (name + '.py'))
            value = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = value
            spec.loader.exec_module(value)
            return value
        self.catalog = load('catalog')
        self.menu = load('menu')
        self.state = NS(clear=AsyncMock())
        self.message = Message(from_user=NS(id=999), text='bot text', answer=AsyncMock(), bot=object())
        self.callback = NS(message=self.message, from_user=NS(id=42), bot=self.message.bot, answer=AsyncMock())

    def add(self, name='example', handler='menu', commands=None, enabled=True, folder=None):
        router = Router()
        async def entry(message, state): pass
        entry.__name__ = handler
        router(Command(*(commands if commands is not None else [name])))(entry)
        result = NS(uuid=uuid4(), enabled=enabled, meta=NS(name=name), _dir_name=folder or name, telegram_bot_routers=[router])
        self.registry.append(result)
        return result, router

    def open(self, mod):
        self.callback.data = f'mm:open:{mod.uuid}'
        asyncio.run(self.menu.open_module(self.callback, self.state))

    def test_command_opens_catalog_for_authorized_user(self):
        self.message.from_user.id = 42
        asyncio.run(self.menu.command_menu(self.message, self.state))
        self.state.clear.assert_awaited_once()
        self.assertIn('Установлено: 0', self.message.answer.call_args.args[0])

    def test_catalog_pagination_and_short_callbacks(self):
        for index in range(15): self.add(name=f'module{index}')
        asyncio.run(self.menu.show_catalog(self.message))
        rows = self.message.answer.call_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual(len([b for row in rows for b in row if b.callback_data.startswith('mm:open:')]), 12)
        self.assertTrue(all(len(row) <= 2 for row in rows))
        self.assertTrue(all(len(b.callback_data.encode()) <= 64 for row in rows for b in row))
        self.assertEqual(self.catalog.module_page(self.registry, 999)[1:], (1, 2))

    def test_all_current_module_commands_resolve(self):
        for folder, name, commands, expected in (
            ('kosell_module', 'kosellrent', ['kosellrent'], 'kosellrent'),
            ('holdboost_module', 'holdboost', ['holdboost_import', 'holdboost'], 'holdboost'),
            ('autobump_groups', 'bumpgroups', ['bumpgroups'], 'bumpgroups'),
            ('twofa_module', 'twofa', ['twofa'], 'twofa'),
            ('playerok_profit_report_module', 'profitreport', ['profit', 'profitreport'], 'profitreport'),
            ('playerok_deals_report_module', 'dealsreport', ['deals', 'deals_data', 'deals_report'], 'deals_data')):
            mod, _ = self.add(name=name, commands=commands, folder=folder)
            self.assertEqual(self.catalog.settings_command(mod), expected)

    def test_target_module_gets_clicking_user_and_state(self):
        mod, router = self.add()
        self.open(mod)
        kwargs = router.propagate_event.call_args.kwargs
        self.assertEqual(kwargs['event'].from_user.id, 42)
        self.assertEqual(kwargs['event'].text, '/example')
        self.assertIs(kwargs['event'].bot, self.callback.bot)
        self.assertIs(kwargs['state'], self.state)
        self.state.clear.assert_awaited_once()

    def test_unauthorized_click_does_not_open_settings(self):
        mod, router = self.add()
        self.callback.from_user.id = 1
        self.open(mod)
        router.propagate_event.assert_not_awaited()
        self.message.answer.assert_not_awaited()
        self.assertTrue(self.callback.answer.call_args.kwargs['show_alert'])

    def test_disabled_module_is_not_invoked(self):
        mod, router = self.add(enabled=False)
        self.open(mod)
        router.propagate_event.assert_not_awaited()
        self.assertIn('выключен', self.message.answer.call_args.args[0])

    def test_no_settings_or_unsafe_command_falls_back(self):
        mod, router = self.add(handler='delete_all', commands=['erase'])
        self.open(mod)
        router.propagate_event.assert_not_awaited()
        self.assertIn('нет отдельного меню', self.message.answer.call_args.args[0])

    def test_nested_router_menu_discovery(self):
        mod, router = self.add(handler='other', commands=['other'])
        _, child = self.add(name='nested')
        router.sub_routers.append(child)
        self.assertEqual(self.catalog.settings_command(mod), 'nested')

    def test_failed_target_shows_fallback(self):
        mod, router = self.add()
        router.propagate_event.side_effect = RuntimeError('offline error')
        self.open(mod)
        self.assertIn('Не удалось открыть', self.message.answer.call_args.args[0])

    def test_disappeared_module_refreshes_catalog(self):
        mod, router = self.add()
        self.registry.clear()
        self.open(mod)
        router.propagate_event.assert_not_awaited()
        self.assertIn('Установлено: 0', self.message.answer.call_args.args[0])

    def test_unauthorized_command_requests_authentication(self):
        asyncio.run(self.menu.command_menu(self.message, self.state))
        self.auth.assert_awaited_once_with(self.message, self.state)
        self.message.answer.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
