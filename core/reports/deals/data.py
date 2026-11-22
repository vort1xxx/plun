from .. import storage

DEFAULTS = {'deals': {'deals': []}, 'daily_reports': {'reports': {}, 'sent': {}}}


class Data:
    @staticmethod
    def get(name):
        def validate(value):
            if name == 'deals' and not isinstance(value.get('deals'), list):
                raise ValueError('Некорректная история сделок')
            if name == 'daily_reports' and (not isinstance(value.get('reports'), dict) or not isinstance(value.get('sent'), dict)):
                raise ValueError('Некорректная история ежедневных отчётов')
        return storage.read(f'bot_data/reports/{name}.json', DEFAULTS[name],
            f'modules/playerok_deals_report_module/module_data/{name}.json', validate)

    @staticmethod
    def set(name, value):
        if name not in DEFAULTS:
            raise ValueError('Unknown report data file')
        storage.write(storage.ROOT / f'bot_data/reports/{name}.json', value)
        return value
