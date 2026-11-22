"""Offline checks; no live bot or paid requests."""
import asyncio
from datetime import datetime
from enum import Enum
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "bumpgroups_offline"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package


def load(name, path):
    spec = importlib.util.spec_from_file_location(PACKAGE + "." + name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


logic = load("logic", "logic.py")
storage = load("storage", "storage.py")


class Status(Enum):
    APPROVED = 0
    SOLD = 1


class Priority(Enum):
    PREMIUM = 0
    DEFAULT = 1


enums = ModuleType("playerokapi.enums")
enums.ItemStatuses, enums.PriorityTypes = Status, Priority
settings = ModuleType("settings")
settings.Settings = Mock()
with patch.dict(sys.modules, {"playerokapi": ModuleType("playerokapi"), "playerokapi.enums": enums, "settings": settings}):
    worker = load("worker", "worker.py")


def ts(time, day=10):
    return datetime.fromisoformat(f"2026-10-{day:02d}T{time}").replace(tzinfo=logic.LOCAL_TZ).timestamp()


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.addCleanup(self.temp.cleanup)
        override = patch.object(storage, "ROOT", Path(self.temp.name))
        override.start()
        self.addCleanup(override.stop)
        settings.Settings.get.return_value = {}
        worker.ACTIVE.set()
        self.addCleanup(worker.ACTIVE.clear)
        sleep_patch = patch.object(worker.time, "sleep")
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        self.group = dict(id="123456789abc", name="Steam", enabled=True,
                          window_start="18:00", window_end="20:00", check_interval_seconds=60,
                          position=5, items=[dict(id="item", name="Game")])
        self.config = dict(enabled=True, groups=[self.group])
        storage.save_config(self.config)
        self.account = Mock(id="seller")
        self.account.get_item.return_value = SimpleNamespace(id="item", name="Game", priority=Priority.PREMIUM,
            user=SimpleNamespace(id="seller"), status=Status.APPROVED, sequence=8, raw_price=100)
        self.account.get_item_priority_statuses.return_value = [SimpleNamespace(id="premium", type=Priority.PREMIUM, price=5)]

    def tick(self, time="18:00:00", day=10):
        worker.tick(self.account, ts(time, day))

    def test_period_parser(self):
        for text, seconds in (("30 сек", 30), ("30 мин", 1800), ("2 ч", 7200), ("1 д", 86400), ("15", 900)):
            self.assertEqual(logic.parse_period(text), seconds)
        for text in ("0", "-1", "31 д", "1.5 ч"):
            with self.assertRaises(ValueError):
                logic.parse_period(text)

    def test_window_validation(self):
        for value in ("18:00 - 20:00", "22:00–02:00", "00:00 - 24:00"):
            self.assertEqual(len(logic.parse_window(value)), 2)
        for value in ("18:00", "18:00 - 18:00", "25:00 - 20:00", "18:60 - 20:00", "24:00 - 02:00"):
            with self.assertRaises(ValueError):
                logic.parse_window(value)

    def test_first_check_at_window_start_and_interval(self):
        self.tick("17:59:59")
        self.account.get_item.assert_not_called()
        self.tick()
        self.account.increase_item_priority_status.assert_called_once_with("item", "premium")
        self.tick("18:00:59")
        self.account.increase_item_priority_status.assert_called_once()
        self.tick("18:01:00")
        self.assertEqual(self.account.increase_item_priority_status.call_count, 2)

    def test_no_cooldown(self):
        self.config['groups'][0]['cooldown_seconds'] = 86400
        storage.save_config(self.config)
        self.assertNotIn('cooldown_seconds', storage.get_config()['groups'][0])
        self.tick()
        self.tick("18:01:00")
        self.assertEqual(self.account.increase_item_priority_status.call_count, 2)

    def test_outside_window_no_remote_calls(self):
        for time in ("00:00:00", "17:59:59", "20:00:00", "23:00:00"):
            self.tick(time)
        self.account.get_item.assert_not_called()
        self.account.increase_item_priority_status.assert_not_called()

    def test_position_comparison_is_strict(self):
        for position in (1, 5):
            self.account.get_item.return_value.sequence = position
            self.tick("18:00:00" if position == 1 else "18:01:00")
        self.account.increase_item_priority_status.assert_not_called()
        self.account.get_item.return_value.sequence = 6
        self.tick("18:02:00")
        self.account.increase_item_priority_status.assert_called_once()

    def test_unknown_or_invalid_position_is_skipped(self):
        for offset, position in enumerate((None, 0, -1, "8", True)):
            self.account.get_item.return_value.sequence = position
            self.tick(f"18:0{offset}:00")
        self.account.increase_item_priority_status.assert_not_called()

    def test_api_priority_position_when_sequence_missing(self):
        item = self.account.get_item.return_value
        item.sequence = None
        item.priority_position = 8
        self.tick()
        self.account.increase_item_priority_status.assert_called_once()
        self.assertEqual(storage.get_state()['items']['item']['position_source'], 'priority_position')

    def test_priority_position_takes_precedence_over_sequence(self):
        self.account.get_item.return_value.priority_position = 5
        self.account.get_item.return_value.sequence = 80
        self.tick()
        self.account.increase_item_priority_status.assert_not_called()
        self.assertEqual(storage.get_state()['items']['item']['position'], 5)

    def test_remote_check_error_shown_in_state(self):
        self.account.get_item.side_effect = ConnectionError()
        self.tick()
        info = storage.get_state()['items']['item']
        self.assertEqual((info['result'], info['error']), ('check_error', 'ConnectionError'))
        self.account.increase_item_priority_status.assert_not_called()

    def test_runtime_diagnostics(self):
        bot = SimpleNamespace(account=self.account)
        with patch.object(worker, '_thread', SimpleNamespace(is_alive=lambda: True)):
            self.assertIn('работает', worker.runtime_status(bot, ts('18:00:00')))
            self.assertIn('Ожидание', worker.runtime_status(bot, ts('17:00:00')))
            self.config['enabled'] = False
            storage.save_config(self.config)
            self.assertIn('выключен', worker.runtime_status(bot))
            self.config.update(enabled=True, groups=[])
            storage.save_config(self.config)
            self.assertIn('Групп нет', worker.runtime_status(bot))

    def test_new_zero_threshold_rejected(self):
        self.group['position'] = 0
        with self.assertRaises(ValueError):
            storage.save_config(self.config)

    def test_overnight_window(self):
        self.group.update(window_start="22:00", window_end="02:00")
        storage.save_config(self.config)
        self.tick("21:59:59")
        self.account.get_item.assert_not_called()
        self.tick("22:00:00")
        self.tick("01:00:00", day=11)
        self.tick("02:00:00", day=11)
        self.assertEqual(self.account.increase_item_priority_status.call_count, 2)
        self.assertEqual(logic.active_window(ts("01:00:00", 11), self.group)[0], ts("22:00:00"))

    def test_full_day_window(self):
        self.group.update(window_start="00:00", window_end="24:00")
        storage.save_config(self.config)
        self.tick("00:00:00")
        self.tick("23:59:59")
        self.assertEqual(self.account.increase_item_priority_status.call_count, 2)

    def test_next_window_starts_without_waiting_interval(self):
        self.group['check_interval_seconds'] = 7200
        storage.save_config(self.config)
        self.tick("19:59:50")
        self.assertEqual(storage.get_state()['items']['item']['next_at'], ts("18:00:00", 11))
        self.tick("18:00:00", 11)
        self.assertEqual(self.account.increase_item_priority_status.call_count, 2)

    def test_window_can_close_during_remote_call(self):
        clock = [ts("19:59:59")]
        def get_statuses(*args):
            clock[0] = ts("20:00:00")
            return [SimpleNamespace(id="premium", type=Priority.PREMIUM, price=5)]
        self.account.get_item_priority_statuses.side_effect = get_statuses
        with patch.object(worker.time, "time", side_effect=lambda: clock[0]):
            worker.tick(self.account)
        self.account.increase_item_priority_status.assert_not_called()
        self.assertEqual(storage.get_state()['items']['item']['result'], 'outside_window')

    def test_long_interval_still_starts_next_daily_window(self):
        self.group['check_interval_seconds'] = 7 * 86400
        storage.save_config(self.config)
        self.tick()
        self.assertEqual(storage.get_state()['items']['item']['next_at'], ts('18:00:00', 11))
        self.tick('18:00:00', 11)
        self.assertEqual(self.account.increase_item_priority_status.call_count, 2)

    def test_sold_or_foreign_item_is_skipped(self):
        self.account.get_item.return_value.status = Status.SOLD
        self.tick()
        self.account.get_item.return_value.status = Status.APPROVED
        self.account.get_item.return_value.user.id = "other"
        self.tick("18:01:00")
        self.account.increase_item_priority_status.assert_not_called()

    def test_uncertain_mutation_blocks_future_purchases(self):
        self.account.increase_item_priority_status.side_effect = TimeoutError()
        self.tick()
        self.tick("18:01:00")
        self.tick("18:00:00", 11)
        self.account.increase_item_priority_status.assert_called_once()
        self.assertTrue(storage.get_state()['items']['item']['uncertain'])

    def test_disk_failure_prevents_purchase(self):
        with patch.object(worker, 'save_state', side_effect=OSError("disk full")):
            self.tick()
        self.account.increase_item_priority_status.assert_not_called()

    def test_disabled_and_builtin_enabled_do_not_run(self):
        worker.ACTIVE.clear()
        self.tick()
        worker.ACTIVE.set()
        settings.Settings.get.return_value = {'playerok': {'auto_bump_items': {'enabled': True}}}
        self.tick()
        self.account.get_item.assert_not_called()

    def test_pause_during_remote_read_prevents_paid_request(self):
        self.account.get_item_priority_statuses.side_effect = lambda *args: (worker.ACTIVE.clear() or [SimpleNamespace(id="premium", type=Priority.PREMIUM, price=5)])
        self.tick()
        self.account.increase_item_priority_status.assert_not_called()

    def test_settings_change_during_read_prevents_purchase(self):
        def edit(*args):
            self.group['position'] = 20
            storage.save_config(self.config)
            return [SimpleNamespace(id="premium", type=Priority.PREMIUM, price=5)]
        self.account.get_item_priority_statuses.side_effect = edit
        self.tick()
        self.account.increase_item_priority_status.assert_not_called()

    def test_independent_group_windows_and_intervals(self):
        self.config['groups'].append(dict(self.group, id='abcdef123456', window_start='19:00', window_end='21:00', check_interval_seconds=120, items=[dict(id='second', name='Other')]))
        storage.save_config(self.config)
        self.tick()
        self.assertNotIn('second', storage.get_state()['items'])
        self.tick('19:00:00')
        self.assertEqual(storage.get_state()['items']['item']['next_at'], ts('19:01:00'))
        self.assertEqual(storage.get_state()['items']['second']['next_at'], ts('19:02:00'))

    def test_restart_does_not_repeat_or_catch_up_missed_checks(self):
        self.tick()
        self.tick('18:00:01')
        self.tick('19:00:00')
        self.assertEqual(self.account.increase_item_priority_status.call_count, 2)
        self.assertEqual(storage.get_state()['items']['item']['next_at'], ts('19:01:00'))

    def test_corrupt_state_not_overwritten(self):
        path = Path(self.temp.name) / 'bot_data' / 'autobump_groups' / 'state.json'
        path.parent.mkdir(parents=True)
        path.write_text('broken')
        self.tick()
        self.account.increase_item_priority_status.assert_not_called()
        self.assertEqual(path.read_text(), 'broken')

    def test_duplicate_item_across_groups_rejected(self):
        self.config['groups'].append(dict(self.group, id='abcdef123456'))
        with self.assertRaises(ValueError):
            storage.save_config(self.config)

    def test_default_priority_skipped_like_core_bot(self):
        self.account.get_item.return_value.priority = Priority.DEFAULT
        self.tick()
        self.account.increase_item_priority_status.assert_not_called()

    def test_plugin_settings_migrate_once(self):
        import json
        target = storage.ROOT / 'bot_settings/autobump_groups.json'
        target.unlink()
        legacy = storage.ROOT / 'modules/autobump_groups/module_settings/config.json'
        legacy.parent.mkdir(parents=True)
        legacy.write_text(json.dumps(self.config), encoding='utf-8-sig')
        self.assertEqual(storage.get_config(), self.config)
        storage.save_config(dict(enabled=False, groups=[]))
        self.assertEqual(storage.get_config(), dict(enabled=False, groups=[]))
        self.assertEqual(json.loads(legacy.read_text(encoding='utf-8-sig')), self.config)

    def test_uncertain_payment_state_survives_migration(self):
        import json
        legacy = storage.ROOT / 'modules/autobump_groups/module_data/state.json'
        legacy.parent.mkdir(parents=True)
        state = {'items': {'item': {'uncertain': True, 'result': 'uncertain'}}}
        legacy.write_text(json.dumps(state), encoding='utf-8')
        self.assertEqual(storage.get_state(), state)
        self.tick()
        self.account.increase_item_priority_status.assert_not_called()

    def test_builtin_lifecycle_activates_scheduler(self):
        lifecycle = load('lifecycle', '__init__.py')
        lifecycle.__package__ = PACKAGE
        lifecycle.__spec__ = None
        worker.ACTIVE.clear()
        bot = SimpleNamespace(account=self.account)
        with patch.object(worker, 'start') as start, patch.dict(sys.modules, {PACKAGE + '.worker': worker}):
            asyncio.run(lifecycle.on_playerok_init(bot))
        self.assertTrue(worker.ACTIVE.is_set())
        start.assert_called_once_with(bot)

    def test_builtin_hooks_registered_once(self):
        lifecycle = load('lifecycle', '__init__.py')
        lifecycle.__package__ = PACKAGE
        lifecycle.__spec__ = None
        handler, configure = Mock(), Mock()
        overrides = {'core.handlers': SimpleNamespace(add_bot_event_handler=handler),
                     PACKAGE + '.module_logging': SimpleNamespace(configure_logging=configure)}
        with patch.dict(sys.modules, overrides):
            lifecycle.register()
            lifecycle.register()
        configure.assert_called_once()
        self.assertEqual(handler.call_count, 2)
        handler.assert_any_call('ON_PLAYEROK_BOT_INIT', lifecycle.on_playerok_init)
        handler.assert_any_call('ON_TELEGRAM_BOT_INIT', lifecycle.on_telegram_init)

    def test_first_premium_or_paid_offer_used_without_price_limit(self):
        self.account.get_item_priority_statuses.return_value = [SimpleNamespace(id='paid', type=Priority.DEFAULT, price=100), SimpleNamespace(id='cheap', type=Priority.PREMIUM, price=2)]
        self.tick()
        self.account.increase_item_priority_status.assert_called_once_with('item', 'paid')

    def test_legacy_config_migration_removes_old_options(self):
        old = dict(self.group, interval_seconds=300, position=0, max_price='1', notify=True, mode='links', included=[], excluded=[], cooldown_seconds=60)
        old.pop('check_interval_seconds')
        old.pop('window_start')
        old.pop('window_end')
        migrated = logic.validate_config(dict(enabled=True, groups=[old]))['groups'][0]
        self.assertEqual((migrated['window_start'], migrated['window_end']), ('18:00', '20:00'))
        self.assertEqual(migrated['check_interval_seconds'], 300)
        self.assertEqual(migrated['position'], 1)
        for key in ('mode', 'included', 'excluded', 'notify', 'max_price', 'interval_seconds', 'cooldown_seconds'):
            self.assertNotIn(key, migrated)
        old['mode'] = 'all'
        self.assertFalse(logic.validate_config(dict(enabled=True, groups=[old]))['groups'][0]['enabled'])

    def test_menu_edits_time_and_frequency_and_uses_two_columns(self):
        class Filter:
            def __getattr__(self, name): return self
            def __eq__(self, value): return self
            def startswith(self, value): return self
        class Router:
            def __init__(self): self.handlers = []
            def message(self, *filters):
                def register(function):
                    self.handlers.append(function)
                    return function
                return register
            callback_query = message
        def module(name, **values):
            value = ModuleType(name)
            value.__dict__.update(values)
            return value
        overrides = {
            PACKAGE + '.worker': worker,
            'aiogram': module('aiogram', F=Filter(), Router=Router, types=SimpleNamespace(Message=object, CallbackQuery=object)),
            'aiogram.filters': module('aiogram.filters', Command=lambda name: name),
            'aiogram.fsm.context': module('aiogram.fsm.context', FSMContext=object),
            'aiogram.fsm.state': module('aiogram.fsm.state', State=object, StatesGroup=object),
            'aiogram.types': module('aiogram.types', BufferedInputFile=Mock(), InlineKeyboardButton=SimpleNamespace, InlineKeyboardMarkup=SimpleNamespace),
            'settings': settings,
            'tgbot': module('tgbot', callback_datas=SimpleNamespace(MenuNavigation=lambda **kw: SimpleNamespace(pack=lambda: 'menu:' + kw['to']))),
            'tgbot.helpful': module('tgbot.helpful', do_auth=AsyncMock()),
            'plbot.playerokbot': module('plbot.playerokbot', get_playerok_bot=Mock()),
        }
        with patch.dict(sys.modules, overrides):
            menu = load('menu', 'menu.py')
            registered = {function.__name__ for function in menu.router.handlers}
            self.assertIn('manual_raise', registered)
            self.assertFalse({'mode_prompt', 'set_mode', 'notify_toggle'} & registered)
            settings.Settings.get.return_value = {'telegram': {'bot': {'signed_users': ['admin']}}}
            message = SimpleNamespace(text='22:00 - 02:00', from_user=SimpleNamespace(id='admin'), answer=AsyncMock())
            state = SimpleNamespace(get_data=AsyncMock(return_value=dict(gid=self.group['id'], field='window')), clear=AsyncMock())
            asyncio.run(menu.edit_value(message, state))
            group = storage.get_config()['groups'][0]
            self.assertEqual((group['window_start'], group['window_end']), ('22:00', '02:00'))
            message.text = '30 сек'
            state.get_data.return_value['field'] = 'check_interval_seconds'
            asyncio.run(menu.edit_value(message, state))
            self.assertEqual(storage.get_config()['groups'][0]['check_interval_seconds'], 30)
            rows = message.answer.call_args.kwargs['reply_markup'].inline_keyboard
            self.assertTrue(all(len(row) == 2 for row in rows[:-1]))
            for button in [b for row in rows for b in row]:
                self.assertLessEqual(len(button.callback_data.encode()), 64)
                self.assertFalse(any(key in button.callback_data for key in ('max_price', 'included', 'excluded', 'notify', 'mode', 'cooldown')))
            self.account.increase_item_priority_status.assert_not_called()


    def test_manual_raise_ignores_pause_window_position_and_deadline(self):
        self.group['enabled'] = False
        self.config['enabled'] = False
        storage.save_config(self.config)
        self.account.get_item.return_value.sequence = 1
        worker.record('item', next_at=ts('18:00:00', 11))
        result = worker.raise_group(self.account, self.group['id'], ts('12:00:00'))
        self.account.increase_item_priority_status.assert_called_once()
        self.assertEqual(result, dict(raised=1, skipped=0, errors=0, uncertain=0))
        self.assertFalse(storage.get_config()['enabled'])
        self.assertFalse(storage.get_config()['groups'][0]['enabled'])

    def test_manual_raise_can_buy_priority_for_ordinary_item(self):
        self.account.get_item.return_value.priority = Priority.DEFAULT
        result = worker.raise_group(self.account, self.group['id'], ts('12:00:00'))
        self.assertEqual(result['raised'], 1)

    def test_manual_raise_respects_uncertain_payment(self):
        worker.record('item', uncertain=True, result='uncertain')
        result = worker.raise_group(self.account, self.group['id'], ts('12:00:00'))
        self.assertEqual(result['uncertain'], 1)
        self.account.get_item.assert_not_called()
        self.account.increase_item_priority_status.assert_not_called()

    def test_manual_raise_does_not_race_scheduler(self):
        worker._tick_lock.acquire()
        try:
            with self.assertRaises(ValueError):
                worker.raise_group(self.account, self.group['id'])
        finally:
            worker._tick_lock.release()
        self.account.increase_item_priority_status.assert_not_called()

    def test_manual_batch_reports_skips_and_continues_after_read_error(self):
        self.group['items'] += [dict(id='second', name='Second'), dict(id='third', name='Third')]
        storage.save_config(self.config)
        first = self.account.get_item.return_value
        second = SimpleNamespace(**vars(first))
        second.status = Status.SOLD
        self.account.get_item.side_effect = [ConnectionError(), second, first]
        result = worker.raise_group(self.account, self.group['id'], ts('12:00:00'))
        self.assertEqual(result, dict(raised=1, skipped=1, errors=1, uncertain=0))
        self.account.increase_item_priority_status.assert_called_once_with('third', 'premium')

    def test_manual_raise_timeout_is_not_retried(self):
        self.account.increase_item_priority_status.side_effect = TimeoutError()
        for _ in range(2):
            result = worker.raise_group(self.account, self.group['id'], ts('12:00:00'))
            self.assertEqual(result['uncertain'], 1)
        self.account.increase_item_priority_status.assert_called_once()

    def test_manual_raise_blocked_when_module_unloaded_or_builtin_enabled(self):
        worker.ACTIVE.clear()
        with self.assertRaises(ValueError):
            worker.raise_group(self.account, self.group['id'])
        worker.ACTIVE.set()
        settings.Settings.get.return_value = {'playerok': {'auto_bump_items': {'enabled': True}}}
        with self.assertRaises(ValueError):
            worker.raise_group(self.account, self.group['id'])
        self.account.get_item.assert_not_called()


if __name__ == '__main__':
    unittest.main()
