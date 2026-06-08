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
    realname = f"{student.first_name} {student.last_name}".strip() if student else sub.submitted_name
    sec = await crud.get_section(sub.section_id) if sub.section_id else None
    sec_line = f"\n   Раздел: {sec.name} · Неделя {sub.week}" if sec else ""

    when = roles.fmt_relative(sub.submitted_at_utc)
    if sub.is_late:
        text = (
            "📤 Новая рабочая тетрадь! ⚠️\n"
            f"   Ученик: {realname}{sec_line}\n"
            f"   Файл: {sub.submitted_name}\n"
            f"   Группа: {group_name}\n"
            f"   Время: {roles.fmt_absolute(sub.submitted_at_utc)} ({when})\n"
            f"   Просрочено: +{sub.late_by_minutes} мин"
        )
    else:
        text = (
            "📤 Новая рабочая тетрадь!\n"
            f"   Ученик: {realname}{sec_line}\n"
            f"   Файл: {sub.submitted_name}\n"
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


def intro_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Я посмотрел(а)", callback_data="intro_done")
    ]])


async def send_student_welcome(bot: Bot, student) -> bool:
    """Приветствие ученику при добавлении/первом старте: куратор, группа, видео-инструкция.

    Возвращает True, если сообщение доставлено (ученик уже запускал бота).
    """
    if not student.user_id:
        return False  # бот не может написать первым — отправим при /start

    group = await crud.get_group(student.group_id)
    curator = await crud.get_user_by_tg(student.curator_id)
    cur_name = (curator.first_name if curator and curator.first_name else "куратор")
    group_name = group.name if group else "—"

    text = (
        f"👋 Привет, {student.first_name}! Тебя добавили в учебного бота.\n\n"
        f"📂 Группа: {group_name}\n"
        f"👨‍🏫 Куратор: {cur_name}\n\n"
    )

    video = await crud.get_setting("intro_video")
    try:
        if video:
            await bot.send_video(
                student.user_id, video,
                caption=text + "📺 Посмотри короткую видео-инструкцию и нажми кнопку ниже 👇",
                reply_markup=intro_keyboard(),
            )
        else:
            # видео ещё не загружено админом — открываем доступ сразу
            await crud.set_intro_watched(student.user_id)
            await bot.send_message(
                student.user_id,
                text + "Можешь пользоваться ботом: смотреть рабочие тетради и сдавать РТ. 🚀",
            )
        await crud.mark_welcomed(student.id)
        return True
    except Exception:
        return False
