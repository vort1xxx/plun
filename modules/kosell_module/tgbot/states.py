from aiogram.fsm.state import State, StatesGroup


class Setup(StatesGroup):
    api_key = State()
    playerok_item = State()
    kosell_search = State()
    rental_hours = State()
    delivery_template = State()
    import_config = State()
