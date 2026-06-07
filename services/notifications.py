"""Уведомления куратору и администратору."""
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_ID
from database import crud
from services import roles


async def notify_submission(bot: Bot, sub_id: int) -> None:
    """Мгновенное уведомление куратору о новой сдаче РТ."""
    sub = await crud.get_submission(sub_id)
    if not sub:
        return
    student = await crud.get_student(sub.student_id)
    group = await crud.get_group(student.group_id) if student else None
    group_name = group.name if group else "—"

    when = roles.fmt_relative(sub.submitted_at_utc)
    if sub.is_late:
        text = (
            "📤 Новая рабочая тетрадь! ⚠️\n"
            f"   Ученик: {sub.submitted_name}\n"
            f"   Группа: {group_name}\n"
            f"   Время: {roles.fmt_absolute(sub.submitted_at_utc)} ({when})\n"
            f"   Просрочено: +{sub.late_by_minutes} мин"
        )
    else:
        text = (
            "📤 Новая рабочая тетрадь!\n"
            f"   Ученик: {sub.submitted_name}\n"
            f"   Группа: {group_name}\n"
            f"   Время: {roles.fmt_absolute(sub.submitted_at_utc)} ({when})"
        )

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📂 Открыть PDF", callback_data=f"open_sub:{sub.id}")
    ]])
    try:
        await bot.send_message(sub.curator_id, text, reply_markup=kb)
    except Exception:
        pass


async def notify_admin(bot: Bot, text: str) -> None:
    try:
        await bot.send_message(ADMIN_ID, text)
    except Exception:
        pass
