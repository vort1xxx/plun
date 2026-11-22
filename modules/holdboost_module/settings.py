import os

from settings import Settings as sett, SettingsFile


CONFIG = SettingsFile(
    name="config",
    path=os.path.join(os.path.dirname(__file__), "module_settings", "config.json"),
    need_restore=True,
    default={
        "holdboost": {
            "api_key": "",
            "base_url": "https://api.holdboost.store",
            "boost_months": 1,
            "poll_seconds": 10,
            "poll_attempts": 90,
            "preset_id": ""
        },
        "playerok": {
            "item_keyword": "BOOST",
            "auto_complete_deal": True,
            "log_states": True
        },
        "boost_mappings": []
    }
)

MESSAGES = SettingsFile(
    name="messages",
    path=os.path.join(os.path.dirname(__file__), "module_settings", "messages.json"),
    need_restore=True,
    default={
        "process": {
            "enabled": True,
            "text": [
                "✅ Ссылка на сервер получена: {server}",
                "Бустов: {amount} на {months} мес.",
                "Начинаю выдачу, ожидайте пару минут."
            ]
        },
        "ask_invite": {
            "enabled": True,
            "text": [
                "🔗 Не нашёл ссылку на Discord сервер в заказе.",
                "Отправьте ссылку вида discord.gg/... и я поставлю бусты."
            ]
        },
        "bad_invite": {
            "enabled": True,
            "text": [
                "Не вижу Discord-ссылку. Отправьте ссылку вида discord.gg/..."
            ]
        },
        "done": {
            "enabled": True,
            "text": [
                "✅ Готово! Выдано {done}/{amount} бустов"
            ]
        },
        "customer_error": {
            "enabled": True,
            "text": [
                "⚠️ Возникла ошибка, я позвал продавца, чтобы он решил проблему."
            ]
        },
        "telegram_error": {
            "enabled": True,
            "text": [
                "Ошибка выдачи бустов",
                "Сделка: {deal_id}",
                "Чат: {chat_id}",
                "Товар: {item_name}",
                "Бустов: {amount}",
                "Сервер: {invite}",
                "Ошибка: {error}"
            ]
        }
    }
)

DATA = [CONFIG, MESSAGES]


class Settings:
    @staticmethod
    def get(name: str) -> dict:
        return sett.get(name, DATA)

    @staticmethod
    def set(name: str, new: list | dict) -> dict:
        return sett.set(name, new, DATA)
