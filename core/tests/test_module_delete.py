"""Exercise real deletion functions offline, using disposable module folders."""
import ast
import asyncio
from pathlib import Path
import sys
import tempfile
from threading import Lock
import time
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[2]


def functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            selected.append(node)
    assert len(selected) == len(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), namespace)


class DeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'core/tests')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'modules/example'
        self.source.mkdir(parents=True)
        (self.source / 'config.json').write_text('{"saved": true}')
        self.parent = NS(sub_routers=[])
        self.router = NS(parent_router=self.parent, _parent_router=self.parent)
        self.parent.sub_routers.append(self.router)
        self.module = NS(uuid=uuid.uuid4(), enabled=True, _dir_name='example', meta=NS(name='Example'), telegram_bot_routers=[self.router])
        self.registry = [self.module]
        async def disable(uid):
            self.module.enabled = False
            return True
        async def enable(uid):
            self.module.enabled = True
            return True
        self.ns = dict(Path=Path, UUID=uuid.UUID, uuid=uuid, _project_root=self.root,
            _delete_lock=Lock(), loaded_modules=self.registry, logger=Mock(),
            get_module_by_uuid=lambda uid: next((m for m in self.registry if m.uuid == uid), None),
            disable_module=AsyncMock(side_effect=disable), enable_module=AsyncMock(side_effect=enable))
        functions(ROOT / 'core/modules.py', {'delete_module'}, self.ns)

    def delete(self):
        return asyncio.run(self.ns['delete_module'](self.module.uuid))

    def test_archive_preserves_data_and_removes_registry_and_router(self):
        destination = self.delete()
        self.assertFalse(self.source.exists())
        self.assertEqual((destination / 'config.json').read_text(), '{"saved": true}')
        self.assertEqual(destination.parent, self.root / 'modules_removed')
        self.assertEqual(self.registry, [])
        self.assertEqual(self.parent.sub_routers, [])
        self.assertIsNone(self.router._parent_router)
        self.ns['disable_module'].assert_awaited_once_with(self.module.uuid)

    def test_disabled_module_still_archived(self):
        self.module.enabled = False
        self.assertTrue(self.delete().is_dir())
        self.ns['disable_module'].assert_not_awaited()

    def test_failed_disable_does_not_move_folder(self):
        self.ns['disable_module'] = AsyncMock(return_value=False)
        with self.assertRaises(RuntimeError): self.delete()
        self.assertTrue(self.source.exists())
        self.assertEqual(self.registry, [self.module])

    def test_failed_move_restores_enabled_module(self):
        with patch.object(Path, 'rename', side_effect=OSError('denied')):
            with self.assertRaises(OSError): self.delete()
        self.assertTrue(self.module.enabled)
        self.assertTrue(self.source.exists())
        self.assertIn(self.router, self.parent.sub_routers)
        self.ns['enable_module'].assert_awaited_once()

    def test_unknown_module_and_repeat_do_not_delete_another(self):
        self.delete()
        with self.assertRaises(ValueError): self.delete()

    def test_traversal_rejected(self):
        self.module._dir_name = '../outside'
        (self.root / 'outside').mkdir()
        with self.assertRaises(ValueError): self.delete()
        self.assertTrue((self.root / 'outside').exists())
        self.ns['disable_module'].assert_not_awaited()

    def test_concurrent_deletion_rejected(self):
        self.ns['_delete_lock'].acquire()
        try:
            with self.assertRaises(ValueError): self.delete()
        finally:
            self.ns['_delete_lock'].release()
        self.assertTrue(self.source.exists())

    def handlers(self):
        import html
        core = ModuleType('core.modules')
        core.delete_module = AsyncMock(return_value=self.root / 'modules_removed/example')
        core.get_module_by_uuid = self.ns['get_module_by_uuid']
        settings = Mock()
        settings.get.return_value = {'telegram': {'bot': {'signed_users': [42]}}}
        env = dict(CallbackQuery=object, FSMContext=object, sett=settings, UUID=uuid.UUID,
            escape=html.escape, time=time, InlineKeyboardMarkup=NS, InlineKeyboardButton=NS,
            templ=NS(back_kb=Mock()), calls=NS(ModulesPagination=lambda **kw: NS(pack=lambda: 'modules:0')))
        functions(ROOT / 'tgbot/callback_handlers/actions_other.py', {
            '_module_delete_authorized', 'callback_delete_module_prompt',
            'callback_delete_module_cancel', 'callback_delete_module_confirm'}, env)
        self.state = NS(get_data=AsyncMock(return_value={}), set_state=AsyncMock(), clear=AsyncMock(), update_data=AsyncMock())
        self.callback = NS(data=f'moddeleteok:{self.module.uuid}', from_user=NS(id=42), answer=AsyncMock(),
            message=NS(answer=AsyncMock(), edit_reply_markup=AsyncMock()))
        return env, core

    def test_unauthorized_confirm_never_deletes(self):
        env, core = self.handlers()
        self.callback.from_user.id = 7
        with patch.dict(sys.modules, {'core.modules': core}):
            asyncio.run(env['callback_delete_module_confirm'](self.callback, self.state))
        core.delete_module.assert_not_awaited()

    def test_missing_expired_or_wrong_confirmation_never_deletes(self):
        env, core = self.handlers()
        for data in ({}, {'delete_module_uuid': str(self.module.uuid), 'delete_module_until': 0},
                     {'delete_module_uuid': str(uuid.uuid4()), 'delete_module_until': time.time() + 100}):
            self.state.get_data.return_value = data
            asyncio.run(env['callback_delete_module_confirm'](self.callback, self.state))
        core.delete_module.assert_not_awaited()

    def test_valid_confirmation_deletes_exact_module(self):
        env, core = self.handlers()
        self.state.get_data.return_value = {'delete_module_uuid': str(self.module.uuid), 'delete_module_until': time.time() + 100}
        with patch.dict(sys.modules, {'core.modules': core}):
            asyncio.run(env['callback_delete_module_confirm'](self.callback, self.state))
        core.delete_module.assert_awaited_once_with(self.module.uuid)
        self.state.clear.assert_awaited_once()
        self.callback.message.edit_reply_markup.assert_awaited_once()

    def test_prompt_escapes_module_name_and_cancel_does_not_delete(self):
        env, core = self.handlers()
        self.module.meta.name = '<Example>'
        self.callback.data = f'moddelete:{self.module.uuid}'
        with patch.dict(sys.modules, {'core.modules': core}):
            asyncio.run(env['callback_delete_module_prompt'](self.callback, self.state))
        self.assertIn('&lt;Example&gt;', self.callback.message.answer.call_args.args[0])
        self.assertEqual(self.state.update_data.call_args.kwargs['delete_module_uuid'], str(self.module.uuid))
        markup = self.callback.message.answer.call_args.kwargs['reply_markup']
        self.assertTrue(all(len(b.callback_data.encode()) <= 64 for row in markup.inline_keyboard for b in row))
        asyncio.run(env['callback_delete_module_cancel'](self.callback, self.state))
        core.delete_module.assert_not_awaited()
        self.assertTrue(self.source.exists())


if __name__ == '__main__': unittest.main()
