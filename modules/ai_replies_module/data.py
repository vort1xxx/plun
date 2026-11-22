import os

from data import Data as data, DataFile


STATS = DataFile(
    name="stats",
    path=os.path.join(os.path.dirname(__file__), "module_data", "stats.json"),
    default={"date": "", "count": 0}
)

DATA = [STATS]


class Data:
    @staticmethod
    def get(name: str) -> dict:
        return data.get(name, DATA)

    @staticmethod
    def set(name: str, new: list | dict) -> dict:
        return data.set(name, new, DATA)
