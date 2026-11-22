from aiogram.fsm.state import State, StatesGroup


class SteamAutoSetup(StatesGroup):
    item_url = State()
    reward_amount = State()
    api_key = State()
    import_config = State()
