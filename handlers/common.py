"""Общие хэндлеры: /start, регистрация по ссылке, определение роли, fallback."""
from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from database import crud
from keyboards.admin_kb import admin_menu
from keyboards.curator_kb import curator_menu
from keyboards.student_kb import student_menu
from services import notifications, roles
from states.fsm_states import JoinStates

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    tg = message.from_user
    payload = (command.args or "").strip()

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

    # ── РЕГИСТРАЦИЯ ПО ССЫЛКЕ ─────────────────────────────────────
    # payload — случайный код группы. Доступно тем, кто ещё не ученик/куратор/админ.
    if payload and role not in ("admin", "curator"):
        group = await crud.get_group_by_token(payload)
        # запасной вариант для старых ссылок вида g5
        if group is None and payload.startswith("g") and payload[1:].isdigit():
            group = await crud.get_group(int(payload[1:]))
        if group is None or not group.is_active:
            await message.answer("❌ Ссылка недействительна. Обратись к своему куратору.")
            return
        gid = group.id
        existing = await crud.get_student_by_tg(tg.id)
        if existing and existing.group_id == gid:
            await message.answer(f"Ты уже в группе «{group.name}». Пользуйся меню 👇",
                                 reply_markup=student_menu())
            return
        curator = await crud.get_user_by_tg(group.curator_id)
        cur_name = (curator.first_name if curator and curator.first_name else "куратор")
        await state.set_state(JoinStates.name)
        await state.update_data(gid=gid)
        await message.answer(
            f"👋 Привет! Ты вступаешь в группу «{group.name}» у куратора {cur_name}.\n\n"
            "✍️ Введи своё имя и фамилию — они будут в системе.\n"
            "📝 Пример: Алмас Берков"
        )
        return

    await _greet(message, role, state)


@router.message(JoinStates.name, F.text)
async def join_enter_name(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    gid = data.get("gid")
    await state.clear()
    group = await crud.get_group(gid)
    if not group or not group.is_active:
        await message.answer("❌ Ссылка недействительна. Обратись к куратору.")
        return
    tg = message.from_user
    parts = message.text.strip().split(maxsplit=1)
    first = parts[0]
    last = parts[1] if len(parts) > 1 else ""

    await crud.upsert_user(tg.id, tg.username, tg.first_name, tg.last_name)
    st = await crud.add_student(first_name=first, last_name=last, username=tg.username,
                                user_id=tg.id, group_id=gid, curator_id=group.curator_id)

    await message.answer(
        f"🎉 Отлично, {first}! Ты почти на финише — тебя записали в группу «{group.name}».")

    video = await crud.get_setting("intro_video")
    if video:
        await notifications.send_student_welcome(bot, st)
    else:
        await crud.set_intro_watched(tg.id)
        await message.answer(
            "Теперь можешь пользоваться ботом: 📚 рабочие тетради и 📤 сдавать РТ.",
            reply_markup=student_menu())

    # уведомим куратора о новом ученике
    try:
        await bot.send_message(
            group.curator_id,
            f"➕ Новый ученик записался по ссылке: {first} {last} → группа «{group.name}».")
    except Exception:
        pass


async def _greet(message: Message, role: str, state: FSMContext):
    tg = message.from_user
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
        await crud.upsert_user(tg.id, tg.username, tg.first_name, tg.last_name)
        group = await crud.get_group(st.group_id)
        curator = await crud.get_user_by_tg(st.curator_id)
        cur_name = (curator.first_name if curator and curator.first_name else "куратора")

        video = await crud.get_setting("intro_video")
        # если есть видео-инструкция и ученик её ещё не отметил — показываем её
        if video and not st.intro_watched:
            await notifications.send_student_welcome(message.bot, st)
            return

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
    role = await roles.get_role(message.from_user.id)
    await message.answer("Окей, прервал текущее действие. Воспользуйся меню 👇")
    await _greet(message, role, state)


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
