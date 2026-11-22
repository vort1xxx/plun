import os
import json
from copy import deepcopy
from pathlib import Path
from settings import set_json

from data import DataFile

RENTAL_ORDERS = DataFile(
    name="rental_orders",
    path=os.path.join(os.path.dirname(__file__), "module_data", "rental_orders.json"),
    default={"orders": {}, "rentals": {}},
)
DATA = [RENTAL_ORDERS]


class Data:
    @staticmethod
    def get(name: str) -> dict | list | None:
        file = next(file for file in DATA if file.name == name)
        path = Path(file.path)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            value = deepcopy(file.default)
            set_json(str(path), value)
            return value
        with path.open(encoding="utf-8") as source:
            return json.load(source)

    @staticmethod
    def set(name: str, new: list | dict):
        file = next(file for file in DATA if file.name == name)
        Path(file.path).parent.mkdir(parents=True, exist_ok=True)
        return set_json(file.path, new)
