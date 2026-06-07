"""Хэндлеры куратора: группы, ученики, кто сдал, дедлайн, тетради, удаление."""
import re

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database import crud
from handlers.filters import RoleFilter
from keyboards.curator_kb import (
    add_choice,
    curator_menu,
    deadline_menu,
    groups_inline,
    weekdays_kb,
)
from services import roles
from states.fsm_states import CuratorStates

router = Router()
router.message.filter(RoleFilter("curator"))
router.callback_query.filter(RoleFilter("curator"))

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


# ─── МОИ ГРУППЫ ─────────────────────────────────────────────────

@router.message(F.text == "📂 Мои группы")
async def my_groups(message: Message, state: FSMContext):
    await state.clear()
    groups = await crud.get_groups(curator_id=message.from_user.id)
    lines = ["📂 Мои группы:"]
    for i, g in enumerate(groups, start=1):
        n = await crud.count_students(g.id)
        lines.append(f"   {i}. {g.name} — {n} учеников")
    if not groups:
        lines.append("   (пока нет групп)")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Создать новую группу", callback_data="cur_new_group")
    ]])
    await message.answer("\n".join(lines), reply_markup=kb)


@router.callback_query(F.data == "cur_new_group")
async def new_group_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(CuratorStates.create_group)
    await call.message.answer("Введи название новой группы:")


@router.message(CuratorStates.create_group, F.text)
async def new_group_save(message: Message, state: FSMContext):
    g = await crud.create_group(message.from_user.id, message.text.strip())
    await state.clear()
    await message.answer(f"✅ Группа «{g.name}» создана.", reply_markup=curator_menu())


# ─── МОИ УЧЕНИКИ ────────────────────────────────────────────────

@router.message(F.text == "👥 Мои ученики")
async def my_students(message: Message, state: FSMContext):
    await state.clear()
    groups = await crud.get_groups(curator_id=message.from_user.id)
    if not groups:
        await message.answer("У тебя пока нет групп. Создай группу в «📂 Мои группы».")
        return
    lines = ["👥 Мои ученики:\n"]
    for g in groups:
        sts = await crud.get_students(curator_id=message.from_user.id, group_id=g.id)
        lines.append(f"📂 {g.name} ({len(sts)} чел.)")
        for st in sts[:40]:
            uname = f" (@{st.username})" if st.username else ""
            lines.append(f"   • {st.first_name} {st.last_name}{uname}")
        if len(sts) > 40:
            lines.append(f"   … и ещё {len(sts) - 40}")
        lines.append("")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="➕ Добавить", callback_data="cur_add_open"),
        InlineKeyboardButton(text="➖ Удалить", callback_data="cur_del_menu"),
    ]])
    await message.answer("\n".join(lines)[:4000], reply_markup=kb)


@router.callback_query(F.data == "cur_add_open")
async def add_open_cb(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.answer("Кого добавить?", reply_markup=add_choice())


# ─── УДАЛЕНИЕ УЧЕНИКОВ / ГРУПП ──────────────────────────────────

@router.callback_query(F.data == "cur_del_menu")
async def del_menu(call: CallbackQuery):
    await call.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Одного ученика", callback_data="cur_del_one")],
        [InlineKeyboardButton(text="📂 Группу целиком", callback_data="cur_del_group")],
    ])
    await call.message.answer("Что удалить?", reply_markup=kb)


@router.callback_query(F.data == "cur_del_one")
async def del_one_pick_group(call: CallbackQuery):
    await call.answer()
    groups = await crud.get_groups(curator_id=call.from_user.id)
    if not groups:
        await call.message.answer("Нет групп.")
        return
    await call.message.answer("📂 Из какой группы удалить ученика?",
                              reply_markup=groups_inline(groups, "cur_delone_grp"))


@router.callback_query(F.data.startswith("cur_delone_grp:"))
async def del_one_list(call: CallbackQuery):
    await call.answer()
    gid = int(call.data.split(":")[1])
    students = await crud.get_students(curator_id=call.from_user.id, group_id=gid)
    if not students:
        await call.message.answer("В этой группе нет учеников.")
        return
    rows = [[InlineKeyboardButton(
        text=f"🗑 {st.first_name} {st.last_name}", callback_data=f"cur_delst:{st.id}")]
        for st in students[:60]]
    await call.message.answer("Кого удалить? (история его работ сохранится)",
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("cur_delst:"))
async def del_one_confirm(call: CallbackQuery):
    await call.answer()
    sid = int(call.data.split(":")[1])
    st = await crud.get_student(sid)
    if not st or st.curator_id != call.from_user.id:
        await call.message.answer("❌ Ученик не найден.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да", callback_data=f"cur_delst_yes:{sid}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cur_cancel"),
    ]])
    await call.message.answer(f"Удалить {st.first_name} {st.last_name}? История его работ сохранится.",
                              reply_markup=kb)


@router.callback_query(F.data.startswith("cur_delst_yes:"))
async def del_one_do(call: CallbackQuery):
    await call.answer()
    sid = int(call.data.split(":")[1])
    st = await crud.get_student(sid)
    if not st or st.curator_id != call.from_user.id:
        await call.message.answer("❌ Ученик не найден.")
        return
    await crud.soft_delete_student(sid)
    await call.message.answer(f"✅ {st.first_name} {st.last_name} удалён из группы.")


@router.callback_query(F.data == "cur_del_group")
async def del_group_pick(call: CallbackQuery):
    await call.answer()
    groups = await crud.get_groups(curator_id=call.from_user.id)
    if not groups:
        await call.message.answer("Нет групп.")
        return
    await call.message.answer("📂 Какую группу удалить целиком?",
                              reply_markup=groups_inline(groups, "cur_delgrp"))


@router.callback_query(F.data.startswith("cur_delgrp:"))
async def del_group_confirm(call: CallbackQuery):
    await call.answer()
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    if not g or g.curator_id != call.from_user.id:
        await call.message.answer("❌ Группа не найдена.")
        return
    n = await crud.count_students(gid)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"cur_delgrp_yes:{gid}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cur_cancel"),
    ]])
    await call.message.answer(
        f"🗑 Удалить группу «{g.name}» вместе с {n} учениками?\n"
        "История сданных работ сохранится в системе.",
        reply_markup=kb)


@router.callback_query(F.data.startswith("cur_delgrp_yes:"))
async def del_group_do(call: CallbackQuery):
    await call.answer()
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    if not g or g.curator_id != call.from_user.id:
        await call.message.answer("❌ Группа не найдена.")
        return
    n = await crud.delete_group(gid)
    await call.message.answer(f"✅ Группа «{g.name}» удалена ({n} учеников). История работ сохранена.")


# ─── ДОБАВЛЕНИЕ УЧЕНИКОВ ────────────────────────────────────────

@router.message(F.text == "➕ Добавить")
async def add_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Кого добавить?", reply_markup=add_choice())


@router.callback_query(F.data == "cur_add_one")
async def add_one_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(CuratorStates.add_one_name)
    await call.message.answer("👤 Введи имя и фамилию ученика.")


@router.message(CuratorStates.add_one_name, F.text)
async def add_one_name(message: Message, state: FSMContext):
    parts = message.text.strip().split(maxsplit=1)
    first = parts[0]
    last = parts[1] if len(parts) > 1 else ""
    await state.update_data(first=first, last=last)
    await state.set_state(CuratorStates.add_one_contact)
    await message.answer("📲 Введи @username или Telegram ID ученика.")


@router.message(CuratorStates.add_one_contact, F.text)
async def add_one_contact(message: Message, state: FSMContext):
    contact = message.text.strip()
    username, user_id = _parse_contact(contact)
    await state.update_data(username=username, user_id=user_id)
    groups = await crud.get_groups(curator_id=message.from_user.id)
    if not groups:
        await message.answer("Сначала создай группу в «📂 Мои группы».")
        await state.clear()
        return
    await state.set_state(CuratorStates.add_one_group)
    await message.answer("📂 В какую группу добавить?",
                         reply_markup=groups_inline(groups, "cur_addone_grp"))


@router.callback_query(CuratorStates.add_one_group, F.data.startswith("cur_addone_grp:"))
async def add_one_finish(call: CallbackQuery, state: FSMContext):
    await call.answer()
    gid = int(call.data.split(":")[1])
    data = await state.get_data()
    g = await crud.get_group(gid)
    st = await crud.add_student(
        first_name=data["first"], last_name=data["last"], username=data.get("username"),
        user_id=data.get("user_id"), group_id=gid, curator_id=call.from_user.id,
    )
    uname = f" (@{st.username})" if st.username else (f" ({st.user_id})" if st.user_id else "")
    await call.message.answer(
        f"✅ {st.first_name} {st.last_name}{uname} добавлен в группу «{g.name}».\n\n"
        "❗ Передай ученику: пусть откроет бота и нажмёт /start — "
        "только после этого бот его узнает (бот не может написать первым)."
    )
    await state.clear()


@router.callback_query(F.data == "cur_add_bulk")
async def add_bulk_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(CuratorStates.add_bulk_list)
    await call.message.answer(
        "👥 Массовое добавление учеников\n\n"
        "Отправь список в формате:\n"
        "@username — Имя Фамилия\n"
        "123456789 — Имя Фамилия\n\n"
        "До 100 человек за одно сообщение. Для тысяч — отправляй такими списками "
        "по очереди, бот примет все.\n\n"
        "📝 Пример:\n"
        "@almas_b — Алмас Берков\n"
        "@dana_n — Дана Нурова\n"
        "987654321 — Тимур Асанов"
    )


@router.message(CuratorStates.add_bulk_list, F.text)
async def add_bulk_parse(message: Message, state: FSMContext):
    rows = [r.strip() for r in message.text.splitlines() if r.strip()]
    parsed = []
    errors = 0
    for r in rows[:100]:
        m = re.split(r"\s*[—\-–]\s*", r, maxsplit=1)
        if len(m) != 2:
            errors += 1
            continue
        contact, name = m[0].strip(), m[1].strip()
        username, user_id = _parse_contact(contact)
        name_parts = name.split(maxsplit=1)
        first = name_parts[0]
        last = name_parts[1] if len(name_parts) > 1 else ""
        parsed.append({"username": username, "user_id": user_id, "first": first, "last": last,
                       "contact": contact})

    if not parsed:
        await message.answer("⚠️ Не удалось распознать ни одной строки.\nФормат: @username — Имя Фамилия")
        return

    await state.update_data(bulk=parsed)
    lines = [f"✅ Распознано {len(parsed)} учеников."]
    for i, p in enumerate(parsed[:15], start=1):
        lines.append(f"   {i}. {p['contact']} — {p['first']} {p['last']}")
    if len(parsed) > 15:
        lines.append(f"   … и ещё {len(parsed) - 15}")
    if errors:
        lines.append(f"⚠️ Не распознано строк: {errors}")
    lines.append("\nВ какую группу добавить?")
    groups = await crud.get_groups(curator_id=message.from_user.id)
    await state.set_state(CuratorStates.add_bulk_group)
    await message.answer("\n".join(lines), reply_markup=groups_inline(groups, "cur_bulk_grp"))


@router.callback_query(CuratorStates.add_bulk_group, F.data.startswith("cur_bulk_grp:"))
async def add_bulk_finish(call: CallbackQuery, state: FSMContext):
    await call.answer()
    gid = int(call.data.split(":")[1])
    data = await state.get_data()
    g = await crud.get_group(gid)
    items = data.get("bulk", [])
    n = await crud.add_students_bulk(items, group_id=gid, curator_id=call.from_user.id)
    total = await crud.count_students(gid)
    lines = [f"✅ Добавлено {n} учеников в «{g.name}»."]
    for p in items[:15]:
        tail = f"(@{p['username']})" if p.get("username") else (f"({p['user_id']})" if p.get("user_id") else "")
        lines.append(f"   ✅ {p['first']} {p['last']} {tail}")
    if n > 15:
        lines.append(f"   … и ещё {n - 15}")
    lines.append(f"\nВсего в группе: {total} учеников.")
    lines.append("\n❗ Каждый ученик должен сам открыть бота и нажать /start "
                 "(по тому же @username), тогда бот его узнает. Написать им первым бот не может.")
    await call.message.answer("\n".join(lines)[:4000])
    await state.clear()


# ─── КТО СДАЛ ───────────────────────────────────────────────────

@router.message(F.text == "📥 Кто сдал")
async def who_submitted_pick(message: Message, state: FSMContext):
    await state.clear()
    groups = await crud.get_groups(curator_id=message.from_user.id)
    if not groups:
        await message.answer("У тебя пока нет групп.")
        return
    await message.answer("📋 Выбери группу:", reply_markup=groups_inline(groups, "cur_who"))


@router.callback_query(F.data.startswith("cur_who:"))
async def who_submitted(call: CallbackQuery):
    await call.answer()
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    students = await crud.get_students(curator_id=call.from_user.id, group_id=gid)
    subs = await crud.get_submissions(curator_id=call.from_user.id, group_id=gid)

    # последняя сдача по каждому ученику
    last_by_student: dict[int, object] = {}
    for sub in subs:  # уже отсортировано desc по дате
        if sub.student_id not in last_by_student:
            last_by_student[sub.student_id] = sub

    lines = [f"📋 Группа «{g.name}» — Рабочие тетради\n"]
    on_time = late = missing = 0
    dl_buttons = []
    for i, st in enumerate(students, start=1):
        sub = last_by_student.get(st.id)
        if sub is None:
            lines.append(f"   ❌ {i}. {st.first_name} {st.last_name} — не сдал")
            missing += 1
        elif sub.is_late:
            lines.append(f"   ⚠️ {i}. {st.first_name} {st.last_name} — просрочено\n"
                         f"        {roles.fmt_deadline(sub.submitted_at_utc)} "
                         f"(+{sub.late_by_minutes} мин)")
            late += 1
            dl_buttons.append(InlineKeyboardButton(
                text=f"📄 {st.first_name} ⚠️", callback_data=f"open_sub:{sub.id}"))
        else:
            lines.append(f"   ✅ {i}. {st.first_name} {st.last_name}\n"
                         f"        {roles.fmt_deadline(sub.submitted_at_utc)}")
            on_time += 1
            dl_buttons.append(InlineKeyboardButton(
                text=f"📄 {st.first_name}", callback_data=f"open_sub:{sub.id}"))

    lines.append(f"\nСдали вовремя: {on_time} из {len(students)}")
    lines.append(f"Просрочили: {late} | Не сдали: {missing}")

    # кнопки скачивания PDF по 2 в ряд + очистка списка этой группы
    kb_rows = [dl_buttons[j:j+2] for j in range(0, len(dl_buttons), 2)]
    kb_rows.append([InlineKeyboardButton(
        text="🧹 Очистить список этой группы", callback_data=f"cur_cleargrp:{gid}")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await call.message.answer("\n".join(lines), reply_markup=kb)


@router.callback_query(F.data.startswith("open_sub:"))
async def open_submission(call: CallbackQuery):
    await call.answer()
    sub_id = int(call.data.split(":")[1])
    sub = await crud.get_submission(sub_id)
    if not sub or sub.curator_id != call.from_user.id:
        await call.message.answer("❌ Файл не найден.")
        return
    await call.message.answer_document(
        sub.pdf_file_id,
        caption=f"👤 {sub.submitted_name}\n📤 {roles.fmt_absolute(sub.submitted_at_utc)}"
    )


# ─── РАБОЧИЕ ТЕТРАДИ (просмотр + скачивание) ────────────────────

@router.message(F.text == "📚 Рабочие тетради")
async def curator_workbooks(message: Message, state: FSMContext):
    await state.clear()
    wbs = await crud.get_workbooks()
    if not wbs:
        await message.answer("📚 Администратор пока не загрузил тетради.")
        return
    rows = [[InlineKeyboardButton(text=f"📄 №{w.serial:03d} — {w.topic}",
                                  callback_data=f"wb_send:{w.id}")] for w in wbs]
    await message.answer(
        "📚 Рабочие тетради:\nНажми на нужную — пришлю PDF 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("wb_send:"))
async def curator_send_workbook_btn(call: CallbackQuery):
    await call.answer()
    wb_id = int(call.data.split(":")[1])
    wb = await crud.get_workbook(wb_id)
    if not wb:
        await call.message.answer("❌ Эта тетрадь больше недоступна.")
        return
    await call.message.answer_document(wb.file_id, caption=f"📄 №{wb.serial:03d} — {wb.topic}")


# ─── ДЕДЛАЙН ────────────────────────────────────────────────────

@router.message(F.text == "📅 Дедлайн")
async def deadline_view(message: Message, state: FSMContext):
    await state.clear()
    dl = await crud.get_deadline(message.from_user.id)
    if not dl:
        dl = await crud.upsert_deadline(message.from_user.id)
    local_time = _utc_to_local_hhmm(dl.deadline_time_utc)
    status = "✅ включено" if dl.reminders_enabled else "🔕 выключено"
    text = (
        "📅 Управление дедлайнами\n\n"
        "Текущий дедлайн:\n"
        f"⏰ Каждый(ую) {WEEKDAYS[dl.weekday]} до {local_time}\n\n"
        f"Автонапоминание: {status}\n"
        "  → за 4 часа, за 2 часа и за 30 минут до дедлайна"
    )
    await message.answer(text, reply_markup=deadline_menu(dl.reminders_enabled))


@router.callback_query(F.data == "cur_dl_day")
async def dl_day(call: CallbackQuery):
    await call.answer()
    await call.message.answer("Выбери день дедлайна:", reply_markup=weekdays_kb())


@router.callback_query(F.data.startswith("cur_dl_setday:"))
async def dl_setday(call: CallbackQuery):
    await call.answer()
    wd = int(call.data.split(":")[1])
    await crud.upsert_deadline(call.from_user.id, weekday=wd)
    await call.message.answer(f"✅ День дедлайна: {WEEKDAYS[wd]}.")


@router.callback_query(F.data == "cur_dl_time")
async def dl_time(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(CuratorStates.deadline_time)
    await call.message.answer("Введи время дедлайна по Астане в формате ЧЧ:ММ (например 23:59):")


@router.message(CuratorStates.deadline_time, F.text.regexp(r"^\d{1,2}:\d{2}$"))
async def dl_settime(message: Message, state: FSMContext):
    h, m = map(int, message.text.split(":"))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        await message.answer("⚠️ Неверное время. Формат ЧЧ:ММ, например 23:59.")
        return
    # локальное (UTC+5) → UTC
    utc_h = (h - 5) % 24
    await crud.upsert_deadline(message.from_user.id, deadline_time_utc=f"{utc_h:02d}:{m:02d}")
    await state.clear()
    await message.answer(f"✅ Время дедлайна: {h:02d}:{m:02d} (по Астане).",
                         reply_markup=curator_menu())


@router.callback_query(F.data == "cur_dl_toggle")
async def dl_toggle(call: CallbackQuery):
    await call.answer()
    dl = await crud.get_deadline(call.from_user.id)
    new_val = not (dl.reminders_enabled if dl else True)
    await crud.upsert_deadline(call.from_user.id, reminders_enabled=new_val)
    await call.message.answer("🔔 Напоминания включены." if new_val else "🔕 Напоминания отключены.")


# ─── УДАЛИТЬ ЗАПИСЬ ─────────────────────────────────────────────

@router.message(F.text == "🗑 Удалить запись")
async def delete_record(message: Message, state: FSMContext):
    await state.clear()
    subs = await crud.get_submissions(curator_id=message.from_user.id)
    if not subs:
        await message.answer("Нечего убирать — список пуст.")
        return
    rows = [
        [InlineKeyboardButton(text="🧹 Очистить ВЕСЬ список", callback_data="cur_clear_all")],
        [InlineKeyboardButton(text="📂 Очистить одну группу", callback_data="cur_clear_grp")],
    ]
    for sub in subs[:20]:
        rows.append([InlineKeyboardButton(
            text=f"🗑 {sub.submitted_name} · {roles.to_local(sub.submitted_at_utc):%d.%m %H:%M}",
            callback_data=f"cur_delsub:{sub.id}")])
    await message.answer(
        "🗑 Что убрать из своего списка?\n"
        "Файлы у учеников при этом ОСТАЮТСЯ — скрывается только у тебя.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("cur_delsub:"))
async def delete_confirm(call: CallbackQuery):
    await call.answer()
    sub_id = int(call.data.split(":")[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да", callback_data=f"cur_delyes:{sub_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cur_cancel"),
    ]])
    await call.message.answer("Убрать запись из своего списка? У ученика работа останется.",
                              reply_markup=kb)


@router.callback_query(F.data.startswith("cur_delyes:"))
async def delete_do(call: CallbackQuery):
    await call.answer()
    sub_id = int(call.data.split(":")[1])
    await crud.hide_submission_for_curator(sub_id)
    await call.message.answer("✅ Убрано из твоего списка. У ученика работа осталась.")


# ─── ОЧИСТИТЬ ВЕСЬ СПИСОК ───────────────────────────────────────

@router.callback_query(F.data == "cur_clear_all")
async def clear_all_confirm(call: CallbackQuery):
    await call.answer()
    subs = await crud.get_submissions(curator_id=call.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, очистить", callback_data="cur_clearall_yes"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cur_cancel"),
    ]])
    await call.message.answer(
        f"🧹 Очистить весь твой список ({len(subs)} работ)?\n\n"
        "❗ Это уберёт ВСЕ конспекты из твоего журнала.\n"
        "✅ У учеников их работы при этом ОСТАНУТСЯ — они ничего не потеряют.",
        reply_markup=kb)


@router.callback_query(F.data == "cur_clearall_yes")
async def clear_all_do(call: CallbackQuery):
    await call.answer()
    n = await crud.hide_all_for_curator(call.from_user.id)
    await call.message.answer(
        f"✅ Список очищен — убрано {n} работ.\n"
        "У учеников все конспекты на месте (в разделе «📁 Мои работы»).")


# ─── ОЧИСТИТЬ ОДНУ ГРУППУ ───────────────────────────────────────

@router.callback_query(F.data == "cur_clear_grp")
async def clear_grp_pick(call: CallbackQuery):
    await call.answer()
    groups = await crud.get_groups(curator_id=call.from_user.id)
    if not groups:
        await call.message.answer("У тебя нет групп.")
        return
    await call.message.answer("📂 Список какой группы очистить?",
                              reply_markup=groups_inline(groups, "cur_cleargrp"))


@router.callback_query(F.data.startswith("cur_cleargrp:"))
async def clear_grp_confirm(call: CallbackQuery):
    await call.answer()
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    subs = await crud.get_submissions(curator_id=call.from_user.id, group_id=gid)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, очистить", callback_data=f"cur_cleargrpyes:{gid}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cur_cancel"),
    ]])
    await call.message.answer(
        f"🧹 Очистить список группы «{g.name}» ({len(subs)} работ)?\n"
        "✅ У учеников работы останутся.",
        reply_markup=kb)


@router.callback_query(F.data.startswith("cur_cleargrpyes:"))
async def clear_grp_do(call: CallbackQuery):
    await call.answer()
    gid = int(call.data.split(":")[1])
    n = await crud.hide_all_for_curator(call.from_user.id, group_id=gid)
    g = await crud.get_group(gid)
    await call.message.answer(
        f"✅ Список группы «{g.name}» очищен — убрано {n} работ.\n"
        "У учеников все конспекты на месте.")


@router.callback_query(F.data == "cur_cancel")
async def cur_cancel(call: CallbackQuery):
    await call.answer("Отменено")
    await call.message.answer("❌ Отменено.")


# ─── helpers ────────────────────────────────────────────────────

def _parse_contact(contact: str) -> tuple[str | None, int | None]:
    contact = contact.strip()
    if contact.startswith("@"):
        return contact[1:], None
    if contact.isdigit():
        return None, int(contact)
    return contact or None, None


def _utc_to_local_hhmm(hhmm_utc: str) -> str:
    h, m = map(int, hhmm_utc.split(":"))
    return f"{(h + 5) % 24:02d}:{m:02d}"
