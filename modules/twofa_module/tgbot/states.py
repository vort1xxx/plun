from aiogram.fsm.state import State, StatesGroup


class Setup(StatesGroup):
    item = State()
    label = State()
    secret = State()
    hours = State()
    template = State()
    command = State()
    import_config = State()
