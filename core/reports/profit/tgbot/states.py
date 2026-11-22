from aiogram.fsm.state import State, StatesGroup


class ReportSetup(StatesGroup):
    period = State()
    daily_time = State()
