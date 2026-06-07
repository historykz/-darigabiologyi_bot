from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="👨‍🏫 Кураторы"), KeyboardButton(text="📚 Рабочие тетради")],
            [KeyboardButton(text="👥 Все ученики"), KeyboardButton(text="📊 Все отчёты")],
            [KeyboardButton(text="💾 Резервная копия"), KeyboardButton(text="📊 Google Sheets")],
        ],
        resize_keyboard=True,
    )


def curators_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Назначить куратора", callback_data="adm_add_curator")],
        [InlineKeyboardButton(text="➖ Удалить куратора", callback_data="adm_del_curator")],
    ])


def backup_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Скачать сейчас", callback_data="adm_backup_now")],
        [InlineKeyboardButton(text="🔄 Восстановить из файла", callback_data="adm_restore")],
    ])


def backup_files() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Скачать JSON", callback_data="adm_bk_json"),
         InlineKeyboardButton(text="📊 Скачать Excel", callback_data="adm_bk_xlsx")],
    ])


def confirm(yes_cb: str, no_cb: str = "cancel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да", callback_data=yes_cb),
        InlineKeyboardButton(text="❌ Отмена", callback_data=no_cb),
    ]])
