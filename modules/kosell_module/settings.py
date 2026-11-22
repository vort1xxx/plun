import os
from copy import deepcopy

from settings import Settings as MainSettings, SettingsFile, set_json

DEFAULT_CONFIG = {
    "playerok": {
        "enabled": True,
        "api_key": "",
        "currency": "RUB",
        "delivery_template": "🎮 {game}\n👤 Логин: {login}\n🔑 Пароль: {password}\n⏱ Срок: {duration}\n🔐 Steam Guard: напишите !код (если аккаунтов несколько: !код логин)",
        "mappings": [],
    }
}

CONFIG = SettingsFile(
    name="config",
    path=os.path.join(os.path.dirname(__file__), "module_settings", "config.json"),
    need_restore=True,
    default=DEFAULT_CONFIG,
)
DATA = [CONFIG]


class Settings:
    @staticmethod
    def get(name: str) -> dict:
        result = MainSettings.get(name, DATA)
        if name != "config":
            return result

        # A HoldBoost config was once copied into this folder. Recover to the
        # KOSell schema instead of letting menu/event handlers fail on missing keys.
        current = result if isinstance(result, dict) else {}
        current_playerok = current.get("playerok")
        default_playerok = DEFAULT_CONFIG["playerok"]
        kosell_markers = {"api_key", "currency", "mappings", "delivery_template"}
        if not isinstance(current_playerok, dict) or not kosell_markers.intersection(current_playerok):
            recovered = deepcopy(DEFAULT_CONFIG)
            Settings.set("config", recovered)
            return recovered

        recovered = deepcopy(DEFAULT_CONFIG)
        recovered["playerok"].update(current_playerok)
        for key, value in current.items():
            if key != "playerok":
                recovered[key] = value
        if recovered != current:
            Settings.set("config", recovered)
        return recovered

    @staticmethod
    def set(name: str, new: list | dict) -> dict:
        file = next(file for file in DATA if file.name == name)
        os.makedirs(os.path.dirname(file.path), exist_ok=True)
        return set_json(file.path, new)
