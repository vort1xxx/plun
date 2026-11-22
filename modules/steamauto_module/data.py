import os

from data import Data as data, DataFile


DEALS = DataFile(
    name="deals",
    path=os.path.join(os.path.dirname(__file__), "module_data", "deals.json"),
    default={}
)

DATA = [DEALS]


class Data:
    @staticmethod
    def get(name: str) -> dict:
        return data.get(name, DATA)

    @staticmethod
    def set(name: str, new: list | dict) -> dict:
        return data.set(name, new, DATA)
