import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'storage.py'
spec = importlib.util.spec_from_file_location('report_storage_test', SOURCE)
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        storage.ROOT = Path(self.temp.name)

    def test_migration_does_not_overwrite_new_data(self):
        legacy = storage.ROOT / 'legacy.json'
        legacy.write_text(json.dumps({'deals': ['старые']}), encoding='utf-8-sig')
        self.assertEqual(storage.read('new.json', {}, 'legacy.json'), {'deals': ['старые']})
        storage.write(storage.ROOT / 'new.json', {'deals': ['новые']})
        self.assertEqual(storage.read('new.json', {}, 'legacy.json'), {'deals': ['новые']})
        self.assertEqual(json.loads(legacy.read_text(encoding='utf-8-sig')), {'deals': ['старые']})

    def test_invalid_legacy_is_not_migrated(self):
        (storage.ROOT / 'legacy.json').write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):
            storage.read('new.json', {}, 'legacy.json')
        self.assertFalse((storage.ROOT / 'new.json').exists())

    def test_invalid_existing_is_not_replaced(self):
        path = storage.ROOT / 'new.json'
        path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(json.JSONDecodeError):
            storage.read('new.json', {'deals': []})
        self.assertEqual(path.read_text(encoding='utf-8'), '{broken')

    def test_defaults_and_atomic_write(self):
        self.assertEqual(storage.read('nested/new.json', {'deals': []}), {'deals': []})
        storage.write(storage.ROOT / 'nested/new.json', {'deals': ['товар']})
        self.assertEqual(storage.read('nested/new.json', {}), {'deals': ['товар']})
        self.assertEqual(len(list((storage.ROOT / 'nested').iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
