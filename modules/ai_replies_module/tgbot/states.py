from aiogram.fsm.state import State, StatesGroup


class AiSetup(StatesGroup):
    api_key = State()
    base_url = State()
    model = State()
    prompt_text = State()
    grp_url = State()
    grp_prompt = State()
    faq_question = State()
    faq_answer = State()
    max_chars = State()
    pause_minutes = State()
    daily_limit = State()
    quiet_hours = State()
    import_config = State()
