from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def curator_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📂 Мои группы"), KeyboardButton(text="👥 Мои ученики")],
            [KeyboardButton(text="📥 Кто сдал"), KeyboardButton(text="📚 Рабочие тетради")],
            [KeyboardButton(text="📅 Дедлайн"), KeyboardButton(text="➕ Добавить")],
            [KeyboardButton(text="🔍 Найти ученика"), KeyboardButton(text="📢 Рассылка")],
            [KeyboardButton(text="🗑 Удалить запись")],
        ],
        resize_keyboard=True,
    )


def add_choice() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👤 Одного ученика", callback_data="cur_add_one"),
        InlineKeyboardButton(text="👥 Сразу несколько", callback_data="cur_add_bulk"),
    ]])


def groups_inline(groups, prefix: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"{i+1}. {g.name}", callback_data=f"{prefix}:{g.id}")]
            for i, g in enumerate(groups)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def deadline_menu(enabled: bool) -> InlineKeyboardMarkup:
    toggle = "🔕 Отключить" if enabled else "🔔 Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить день", callback_data="cur_dl_day")],
        [InlineKeyboardButton(text="✏️ Изменить время", callback_data="cur_dl_time")],
        [InlineKeyboardButton(text=toggle, callback_data="cur_dl_toggle")],
    ])


def weekdays_kb() -> InlineKeyboardMarkup:
    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    row = [InlineKeyboardButton(text=d, callback_data=f"cur_dl_setday:{i}")
           for i, d in enumerate(days)]
    return InlineKeyboardMarkup(inline_keyboard=[row])
