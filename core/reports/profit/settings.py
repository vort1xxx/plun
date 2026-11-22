from .. import storage

PATH = 'bot_settings/reports_profit.json'
LEGACY = 'modules/playerok_profit_report_module/module_settings/config.json'
DEFAULT = {'playerok_profit_report': {'daily_enabled': True, 'daily_time': '21:00', 'daily_last_sent': ''}}


class Settings:
    @staticmethod
    def get(name):
        if name != 'config':
            raise ValueError('Unknown report settings file')
        return storage.read(PATH, DEFAULT, LEGACY)

    @staticmethod
    def set(name, value):
        if name != 'config':
            raise ValueError('Unknown report settings file')
        storage.write(storage.ROOT / PATH, value)
        return value
