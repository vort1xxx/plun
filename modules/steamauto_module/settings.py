import os

from settings import Settings as sett, SettingsFile


CONFIG = SettingsFile(
    name="config",
    path=os.path.join(os.path.dirname(__file__), "module_settings", "config.json"),
    need_restore=True,
    default={
        "steamsmm": {
            "api_key": "",
            "base_url": "https://steamsmm.ru/api",
            "action_type": "reward_pack",
            "poll_seconds": 20,
            "poll_attempts": 90
        },
        "playerok": {
            "item_keyword": "НАГРАД",
            "auto_complete_deal": True,
            "log_states": True
        },
        "item_mappings": []
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
                "✅ Ссылка на профиль Steam получена: {profile}",
                "Наград к отправке: {amount}.",
                "Начинаю отправку, ожидайте несколько минут."
            ]
        },
        "ask_profile": {
            "enabled": True,
            "text": [
                "🔗 Не нашёл ссылку на профиль Steam в заказе.",
                "Отправьте ссылку вида steamcommunity.com/id/... или steamcommunity.com/profiles/...",
                "После этого я отправлю награды на ваш профиль."
            ]
        },
        "bad_profile": {
            "enabled": True,
            "text": [
                "Не вижу ссылку на профиль Steam. Отправьте ссылку вида steamcommunity.com/id/..."
            ]
        },
        "done": {
            "enabled": True,
            "text": [
                "✅ Готово! Отправлено {done}/{amount} наград на ваш профиль Steam."
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
                "Ошибка выдачи наград Steam",
                "Сделка: {deal_id}",
                "Чат: {chat_id}",
                "Товар: {item_name}",
                "Наград: {amount}",
                "Профиль: {profile}",
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
