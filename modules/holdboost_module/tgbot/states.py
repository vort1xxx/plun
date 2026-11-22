from aiogram.fsm.state import State, StatesGroup


class HoldBoostSetup(StatesGroup):
    item_url = State()
    boost_amount = State()
    api_key = State()
    preset_id = State()
    msg_text = State()
    import_config = State()
    manual_invite = State()
    manual_amount = State()
