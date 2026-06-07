from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def student_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📚 Рабочие тетради")],
            [KeyboardButton(text="📤 Сдать РТ")],
            [KeyboardButton(text="📁 Мои работы")],
        ],
        resize_keyboard=True,
    )


def send_pdf_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📄 Отправить в PDF", callback_data="student_make_pdf")
    ]])
