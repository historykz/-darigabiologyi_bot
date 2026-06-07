"""Общие хэндлеры: /start, определение роли, fallback."""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from database import crud
from keyboards.admin_kb import admin_menu
from keyboards.curator_kb import curator_menu
from keyboards.student_kb import student_menu
from services import roles

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    tg = message.from_user

    # активация отложенного назначения куратора по @username
    if tg.username:
        pending = await crud.get_setting(f"pending_curator:{tg.username.lower()}")
        if pending:
            await crud.upsert_user(tg.id, tg.username, tg.first_name, tg.last_name, role="curator")
            await crud.set_setting(f"pending_curator:{tg.username.lower()}", "")

    # привязка ученика, добавленного куратором по @username (узнаём его Telegram ID)
    if tg.username:
        await crud.bind_student_by_username(tg.username, tg.id)

    role = await roles.get_role(tg.id)

    if role == "admin":
        # гарантируем запись админа в users
        await crud.upsert_user(tg.id, tg.username, tg.first_name, tg.last_name, role="admin")
        name = tg.first_name or "Администратор"
        await message.answer(
            f"👨‍💼 Добро пожаловать, {name}! Вы вошли как Администратор.",
            reply_markup=admin_menu(),
        )
        return

    if role == "curator":
        await crud.upsert_user(tg.id, tg.username, tg.first_name, tg.last_name)
        groups = await crud.get_groups(curator_id=tg.id)
        if groups:
            parts = []
            for g in groups:
                n = await crud.count_students(g.id)
                parts.append(f"{g.name} ({n} уч.)")
            gline = ", ".join(parts)
        else:
            gline = "групп пока нет — создай первую 📂"
        await message.answer(
            f"👨‍🏫 Добро пожаловать, {tg.first_name or 'Куратор'}!\nТвои группы: {gline}",
            reply_markup=curator_menu(),
        )
        return

    if role == "student":
        st = await crud.get_student_by_tg(tg.id)
        # привяжем username, если изменился
        await crud.upsert_user(tg.id, tg.username, tg.first_name, tg.last_name)
        group = await crud.get_group(st.group_id)
        curator = await crud.get_user_by_tg(st.curator_id)
        cur_name = (curator.first_name if curator and curator.first_name else "куратора")
        await message.answer(
            f"👋 Привет, {st.first_name}! Ты в группе «{group.name if group else '—'}» "
            f"у куратора {cur_name}.",
            reply_markup=student_menu(),
        )
        return

    # НЕ НАЙДЕН
    await message.answer(
        "👋 Привет! Ты пока не зарегистрирован в системе.\n"
        "Обратись к своему куратору — он добавит тебя в группу."
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Окей, прервал текущее действие. Воспользуйся меню 👇")
    await cmd_start(message, state)


@router.message()
async def fallback(message: Message, state: FSMContext):
    """Срабатывает последним: неизвестная команда → показать меню роли."""
    role = await roles.get_role(message.from_user.id)
    text = "Не понял команду. Воспользуйся меню 👇"
    if role == "admin":
        await message.answer(text, reply_markup=admin_menu())
    elif role == "curator":
        await message.answer(text, reply_markup=curator_menu())
    elif role == "student":
        await message.answer(text, reply_markup=student_menu())
    else:
        await message.answer(
            "👋 Ты пока не зарегистрирован. Обратись к куратору — он добавит тебя в группу."
        )


async def resolve_role(message: Message) -> str:
    return await roles.get_role(message.from_user.id)
