"""FSM-состояния для пошаговых сценариев (aiogram 3.x)."""
from aiogram.fsm.state import State, StatesGroup


class AdminStates(StatesGroup):
    add_curator = State()
    workbook_wait_pdf = State()
    workbook_wait_topic = State()
    restore_wait_file = State()
    gsheet_wait_url = State()


class CuratorStates(StatesGroup):
    create_group = State()
    add_one_name = State()
    add_one_contact = State()
    add_one_group = State()
    add_bulk_list = State()
    add_bulk_group = State()
    deadline_time = State()
    get_workbook = State()


class StudentStates(StatesGroup):
    submit_name = State()
    submit_photos = State()
    get_workbook = State()
