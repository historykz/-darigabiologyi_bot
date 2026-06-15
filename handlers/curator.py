"""Хэндлеры куратора: группы, ученики, кто сдал, дедлайн, тетради, удаление."""
import re
from datetime import datetime, timedelta, timezone

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
from services import notifications, roles
from config import TZ as TZ_ASTANA


def _parse_date(text: str):
    """Парсит ДД.ММ.ГГГГ или ДД.ММ (текущий год). Возвращает date или None."""
    import re as _re
    from datetime import date
    s = text.strip().replace("/", ".").replace("-", ".")
    m = _re.match(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?$", s)
    if not m:
        return None
    d, mo = int(m.group(1)), int(m.group(2))
    y = m.group(3)
    year = roles.now_local().year if not y else (2000 + int(y) if len(y) == 2 else int(y))
    try:
        return date(year, mo, d)
    except ValueError:
        return None
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
    hidden = await crud.get_groups(curator_id=message.from_user.id, only_hidden=True)
    lines = ["📂 Твои группы. Нажми на группу, чтобы открыть её настройки:",
             "(там ссылка для учеников, разделы, переименование, выгрузка, скрытие)\n"]
    rows = []
    for i, g in enumerate(groups, start=1):
        n = await crud.count_students(g.id)
        lines.append(f"   {i}. {g.name} — {n} учеников")
        rows.append([InlineKeyboardButton(text=f"⚙️ {g.name}", callback_data=f"cur_grp:{g.id}")])
    if not groups:
        lines.append("   (активных групп нет — создай новую кнопкой ниже)")
    rows.append([InlineKeyboardButton(text="➕ Создать новую группу", callback_data="cur_new_group")])
    if hidden:
        rows.append([InlineKeyboardButton(text=f"🗄 Скрытые группы ({len(hidden)})",
                                          callback_data="cur_hidden")])
    await message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "cur_hidden")
async def hidden_groups(call: CallbackQuery):
    await call.answer()
    hidden = await crud.get_groups(curator_id=call.from_user.id, only_hidden=True)
    if not hidden:
        await call.message.answer("🗄 Скрытых групп нет.")
        return
    rows = []
    lines = ["🗄 Скрытые группы (архив).",
             "Эти группы не мешают в основном списке. Можно вернуть в любой момент.\n"]
    for g in hidden:
        n = await crud.count_students(g.id)
        lines.append(f"   • {g.name} — {n} учеников")
        rows.append([
            InlineKeyboardButton(text=f"↩️ Вернуть «{g.name}»", callback_data=f"cur_unhide:{g.id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"cur_delgrp:{g.id}"),
        ])
    await call.message.answer("\n".join(lines),
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("cur_unhide:"))
async def unhide_group(call: CallbackQuery):
    await call.answer()
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    if not g or g.curator_id != call.from_user.id:
        await call.message.answer("❌ Группа не найдена.")
        return
    await crud.set_group_hidden(gid, False)
    await call.message.answer(f"↩️ Группа «{g.name}» возвращена в основной список «📂 Мои группы».")


@router.callback_query(F.data.startswith("cur_grp:"))
async def group_settings(call: CallbackQuery):
    await call.answer()
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    if not g or g.curator_id != call.from_user.id:
        await call.message.answer("❌ Группа не найдена.")
        return
    n = await crud.count_students(gid)
    secs = await crud.get_sections(gid)
    sec_names = ", ".join(s.name for s in secs) if secs else "пока нет"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Ссылка для учеников", callback_data=f"cur_link:{gid}")],
        [InlineKeyboardButton(text="📚 Разделы (название/недели)", callback_data=f"cur_secs:{gid}")],
        [InlineKeyboardButton(text="✏️ Переименовать группу", callback_data=f"cur_ren:{gid}")],
        [InlineKeyboardButton(text="📦 Выгрузить работы", callback_data=f"cur_export:{gid}")],
        [InlineKeyboardButton(text="🗄 Скрыть группу (в архив)", callback_data=f"cur_hide:{gid}")],
        [InlineKeyboardButton(text="🗑 Удалить группу", callback_data=f"cur_delgrp:{gid}")],
    ])
    await call.message.answer(
        f"⚙️ Группа «{g.name}»\n"
        f"👥 Учеников: {n}\n"
        f"📚 Разделы: {sec_names}\n\n"
        "Что можно сделать:\n"
        "🔗 Ссылка — отправь её ученикам, они сами запишутся в эту группу.\n"
        "📚 Разделы — создать предмет (Анатомия и т.п.), поменять его название, "
        "число недель РТ или количество практик.\n"
        "✏️ Переименовать — изменить название группы.\n"
        "📦 Выгрузить — получить Excel и все PDF работ.\n"
        "🗄 Скрыть — убрать группу из списка в архив (данные не удаляются, можно вернуть).\n"
        "🗑 Удалить — убрать группу со всеми учениками (история сохранится).",
        reply_markup=kb)


@router.callback_query(F.data.startswith("cur_hide:"))
async def hide_group(call: CallbackQuery):
    await call.answer()
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    if not g or g.curator_id != call.from_user.id:
        await call.message.answer("❌ Группа не найдена.")
        return
    await crud.set_group_hidden(gid, True)
    await call.message.answer(
        f"🗄 Группа «{g.name}» скрыта и перемещена в архив.\n"
        "Она пропала из основного списка, но НЕ удалена — ученики и работы на месте.\n"
        "Найти и вернуть её можно в «📂 Мои группы → 🗄 Скрытые группы».")


# ── переименование группы ──
@router.callback_query(F.data.startswith("cur_ren:"))
async def rename_group_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    if not g or g.curator_id != call.from_user.id:
        await call.message.answer("❌ Группа не найдена.")
        return
    await state.set_state(CuratorStates.rename_group)
    await state.update_data(ren_gid=gid)
    await call.message.answer(
        f"✏️ Сейчас группа называется «{g.name}».\n"
        "Напиши новое название одним сообщением — и я сразу его сохраню.\n"
        "Например: 4 поток")


@router.message(CuratorStates.rename_group, F.text)
async def rename_group_do(message: Message, state: FSMContext):
    data = await state.get_data()
    gid = data.get("ren_gid")
    new_name = message.text.strip()[:128]
    await crud.rename_group(gid, new_name)
    await state.clear()
    await message.answer(f"✅ Готово! Группа теперь называется «{new_name}».\n"
                         "Ссылка для учеников осталась прежней — менять её не нужно.")


# ── разделы группы: список и редактирование ──
@router.callback_query(F.data.startswith("cur_secs:"))
async def group_sections(call: CallbackQuery):
    await call.answer()
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    if not g or g.curator_id != call.from_user.id:
        await call.message.answer("❌ Группа не найдена.")
        return
    secs = await crud.get_sections(gid)
    rows = [[InlineKeyboardButton(text=f"📚 {s.name} · РТ: {s.weeks} нед · практик: {s.practices}",
                                  callback_data=f"cur_secedit:{s.id}")] for s in secs]
    rows.append([InlineKeyboardButton(text="➕ Добавить раздел", callback_data=f"cur_addsec:{gid}")])
    await call.message.answer(
        f"📚 Разделы группы «{g.name}».\n"
        "Раздел — это предмет (например, Анатомия). Внутри него ученики сдают РТ по неделям "
        "и практику.\n\nНажми на раздел, чтобы изменить его, или создай новый 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("cur_secedit:"))
async def section_edit(call: CallbackQuery):
    await call.answer()
    sid = int(call.data.split(":")[1])
    sec = await crud.get_section(sid)
    if not sec or sec.curator_id != call.from_user.id:
        await call.message.answer("❌ Раздел не найден.")
        return
    total = roles.section_total_weeks(sec)
    cur = roles.section_current_week(sec)
    period = getattr(sec, "period_days", 7) or 7
    cur_txt = ("ещё не начался" if cur == 0 else
               (f"идёт неделя {cur} из {total}" if cur <= total else "все недели прошли"))
    lines = [
        f"📚 Раздел «{sec.name}»",
        f"📆 Начало: {roles.section_start_str(sec)} → Конец: {roles.section_end_str(sec)}",
        f"⏱ Период: {period} дн/неделю · Недель: {total} · {cur_txt}",
        f"📷 Практик: {sec.practices}",
        "",
    ]
    for w in range(1, total + 1):
        mark = " ◀ сейчас" if w == cur else ""
        lines.append(f"   Неделя {w}: {roles.section_week_range_str(sec, w)} → до {roles.section_deadline_str(sec, w)}{mark}")
    lines += ["", "Выбери, что изменить 👇", "Уже сданные работы не пропадут."]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Переименовать раздел", callback_data=f"cur_secren:{sid}")],
        [InlineKeyboardButton(text="📆 Изменить дату начала", callback_data=f"cur_secdate:{sid}:start")],
        [InlineKeyboardButton(text="📆 Изменить дату конца", callback_data=f"cur_secdate:{sid}:end")],
        [InlineKeyboardButton(text="📷 Изменить число практик", callback_data=f"cur_secprc:{sid}")],
        [InlineKeyboardButton(text="🗑 Удалить раздел", callback_data=f"cur_delsec:{sid}")],
    ])
    await call.message.answer("\n".join(lines)[:4000], reply_markup=kb)


@router.callback_query(F.data.startswith("cur_secdate:"))
async def section_setdate_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    parts = call.data.split(":")  # cur_secdate:{sid}:{start|end}
    sid = int(parts[1])
    which_end = parts[2] if len(parts) > 2 else "start"
    await state.set_state(CuratorStates.sec_setdate)
    await state.update_data(secdate_id=sid, secdate_which=which_end)
    label = "конечную дату (финальный дедлайн)" if which_end == "end" else "дату начала"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📆 Сегодня", callback_data="cur_setdate:today"),
         InlineKeyboardButton(text="📆 Завтра", callback_data="cur_setdate:tomorrow")],
        [InlineKeyboardButton(text="📆 С понедельника", callback_data="cur_setdate:monday")],
    ])
    await call.message.answer(
        f"📆 Введи {label}\nВыбери кнопкой или напиши: ДД.ММ.ГГГГ 👇",
        reply_markup=kb)


@router.callback_query(CuratorStates.sec_setdate, F.data.startswith("cur_setdate:"))
async def section_setdate_btn(call: CallbackQuery, state: FSMContext):
    await call.answer()
    which = call.data.split(":")[1]
    today = roles.now_local().date()
    if which == "today": d = today
    elif which == "tomorrow": d = today + timedelta(days=1)
    else: d = today + timedelta(days=(0 - today.weekday()) % 7)
    await _apply_section_date(call.message, state, d)


@router.message(CuratorStates.sec_setdate, F.text)
async def section_setdate_text(message: Message, state: FSMContext):
    d = _parse_date(message.text)
    if not d:
        await message.answer("❌ Не понял дату. Напиши ДД.ММ.ГГГГ, например 16.06.2025.")
        return
    await _apply_section_date(message, state, d)


async def _apply_section_date(target, state: FSMContext, d):
    data = await state.get_data()
    sid = data.get("secdate_id")
    which_end = data.get("secdate_which", "start")
    if which_end == "end":
        await crud.update_section(sid, end_date=_local_midnight(d).replace(hour=23, minute=59))
    else:
        await crud.update_section(sid, start_date=_local_midnight(d))
    await state.clear()
    sec = await crud.get_section(sid)
    total = roles.section_total_weeks(sec)
    lines = [f"✅ Готово! Раздел «{sec.name}».",
             f"📆 {roles.section_start_str(sec)} → {roles.section_end_str(sec)} · {total} недель."]
    for w in range(1, total + 1):
        lines.append(f"   Неделя {w}: {roles.section_week_range_str(sec, w)} → до {roles.section_deadline_str(sec, w)}")
    await target.answer("\n".join(lines))


@router.callback_query(F.data.startswith("cur_secren:"))
async def section_rename_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    sid = int(call.data.split(":")[1])
    await state.set_state(CuratorStates.sec_rename)
    await state.update_data(secren_id=sid)
    await call.message.answer("✏️ Напиши новое название раздела одним сообщением.\n"
                              "Например: Зоология")


@router.message(CuratorStates.sec_rename, F.text)
async def section_rename_do(message: Message, state: FSMContext):
    data = await state.get_data()
    sid = data.get("secren_id")
    new_name = message.text.strip()[:128]
    await crud.update_section(sid, name=new_name)
    await state.clear()
    await message.answer(f"✅ Раздел теперь называется «{new_name}». Все сданные работы на месте.")


@router.callback_query(F.data.startswith("cur_secwks:"))
async def section_weeks_pick(call: CallbackQuery):
    await call.answer()
    sid = int(call.data.split(":")[1])
    row1 = [InlineKeyboardButton(text=str(w), callback_data=f"cur_secwkset:{sid}:{w}")
            for w in (3, 4, 5, 6)]
    row2 = [InlineKeyboardButton(text=str(w), callback_data=f"cur_secwkset:{sid}:{w}")
            for w in (7, 8, 9, 10)]
    await call.message.answer(
        "📅 Сколько недель будут сдавать РТ в этом разделе?\n"
        "Нажми на число 👇 (обычно 4–6)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[row1, row2]))


@router.callback_query(F.data.startswith("cur_secwkset:"))
async def section_weeks_set(call: CallbackQuery):
    await call.answer()
    _, sid, w = call.data.split(":")
    await crud.update_section(int(sid), weeks=int(w))
    sec = await crud.get_section(int(sid))
    await call.message.answer(
        f"✅ Готово! В разделе «{sec.name}» теперь {w} недель РТ.\n"
        f"Ученики увидят кнопки «Неделя 1…{w}» при сдаче конспекта.")


@router.callback_query(F.data.startswith("cur_secprc:"))
async def section_prc_pick(call: CallbackQuery):
    await call.answer()
    sid = int(call.data.split(":")[1])
    row = [InlineKeyboardButton(text=str(p), callback_data=f"cur_secprcset:{sid}:{p}")
           for p in (1, 2, 3, 4)]
    await call.message.answer(
        "📷 Сколько практик сдают в этом разделе?\n"
        "Нажми на число 👇 (обычно 2 — два раза в месяц)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[row]))


@router.callback_query(F.data.startswith("cur_secprcset:"))
async def section_prc_set(call: CallbackQuery):
    await call.answer()
    _, sid, p = call.data.split(":")
    await crud.update_section(int(sid), practices=int(p))
    sec = await crud.get_section(int(sid))
    await call.message.answer(
        f"✅ Готово! В разделе «{sec.name}» теперь {p} практик(и).\n"
        f"Ученики увидят кнопки «Практика 1…{p}» при сдаче.")


@router.callback_query(F.data.startswith("cur_link:"))
async def group_link(call: CallbackQuery, bot: Bot):
    await call.answer()
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    if not g or g.curator_id != call.from_user.id:
        await call.message.answer("❌ Группа не найдена.")
        return
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={g.token}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 Сменить ссылку", callback_data=f"cur_newlink:{gid}")
    ]])
    await call.message.answer(
        f"🔗 Ссылка для группы «{g.name}»:\n\n{link}\n\n"
        "Отправь её ученикам этой группы. Они откроют, введут имя и фамилию — "
        "и сами попадут в группу, увидят видео-инструкцию и смогут сдавать РТ.\n"
        "Код в ссылке уникальный и случайный — попасть в группу можно только по ней.\n\n"
        "🔄 Если ссылка попала к лишним людям — нажми «Сменить ссылку»: "
        "старая перестанет работать, и нужно будет разослать новую.",
        reply_markup=kb)


@router.callback_query(F.data.startswith("cur_newlink:"))
async def regen_link_confirm(call: CallbackQuery):
    await call.answer()
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    if not g or g.curator_id != call.from_user.id:
        await call.message.answer("❌ Группа не найдена.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, сменить", callback_data=f"cur_newlink_yes:{gid}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cur_cancel"),
    ]])
    await call.message.answer(
        f"🔄 Сменить ссылку для группы «{g.name}»?\n\n"
        "⚠️ Старая ссылка сразу перестанет работать — кто по ней ещё не зашёл, "
        "по старой больше не попадёт.\n"
        "Уже добавленные ученики останутся в группе, им ничего делать не нужно.\n"
        "Новую ссылку нужно будет разослать заново.",
        reply_markup=kb)


@router.callback_query(F.data.startswith("cur_newlink_yes:"))
async def regen_link_do(call: CallbackQuery, bot: Bot):
    await call.answer()
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    if not g or g.curator_id != call.from_user.id:
        await call.message.answer("❌ Группа не найдена.")
        return
    token = await crud.regenerate_token(gid)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={token}"
    await call.message.answer(
        f"✅ Готово! Новая ссылка для группы «{g.name}»:\n\n{link}\n\n"
        "Старая больше не работает. Разошли эту ссылку ученикам, "
        "которым ещё нужно записаться.")


@router.callback_query(F.data == "cur_new_group")
async def new_group_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(CuratorStates.create_group)
    await call.message.answer(
        "➕ Создаём новую группу.\n"
        "Группа — это твой класс или поток учеников (например «3 поток»).\n"
        "Напиши её название одним сообщением 👇")


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
async def del_one_do(call: CallbackQuery, bot: Bot):
    await call.answer()
    sid = int(call.data.split(":")[1])
    st = await crud.get_student(sid)
    if not st or st.curator_id != call.from_user.id:
        await call.message.answer("❌ Ученик не найден.")
        return
    group = await crud.get_group(st.group_id)
    await crud.purge_student_submissions(sid, by=call.from_user.id)
    await crud.soft_delete_student(sid)
    # уведомляем ученика, если он запускал бота
    if st.user_id:
        try:
            await bot.send_message(
                st.user_id,
                f"🚫 Тебя удалили из группы «{group.name if group else '—'}».\n"
                "Доступ к рабочим тетрадям закрыт. По вопросам обратись к куратору.")
        except Exception:
            pass
    await call.message.answer(
        f"✅ {st.first_name} {st.last_name} удалён. Его работы убраны, доступ к боту закрыт.")


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
async def del_group_do(call: CallbackQuery, bot: Bot):
    await call.answer()
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    if not g or g.curator_id != call.from_user.id:
        await call.message.answer("❌ Группа не найдена.")
        return
    students = await crud.get_students(curator_id=call.from_user.id, group_id=gid)
    # зачистка работ + уведомления
    for st in students:
        await crud.purge_student_submissions(st.id, by=call.from_user.id)
        if st.user_id:
            try:
                await bot.send_message(
                    st.user_id,
                    f"🚫 Группа «{g.name}» расформирована, ты удалён(а) из неё.\n"
                    "Доступ к боту закрыт. По вопросам обратись к куратору.")
            except Exception:
                pass
    n = await crud.delete_group(gid)
    await call.message.answer(
        f"✅ Группа «{g.name}» удалена ({n} учеников). Их работы убраны, доступ закрыт. "
        "История сохранена в системе.")


# ─── ДОБАВЛЕНИЕ УЧЕНИКОВ ────────────────────────────────────────

@router.message(F.text == "➕ Добавить")
async def add_menu(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "➕ Добавляем учеников.\n"
        "Самый простой способ — отправить им ссылку группы (📂 Мои группы → ⚙️ группа → 🔗 Ссылка): "
        "они запишутся сами.\n"
        "Либо добавь вручную — одного или сразу списком 👇", reply_markup=add_choice())


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
async def add_one_finish(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    gid = int(call.data.split(":")[1])
    data = await state.get_data()
    g = await crud.get_group(gid)

    # антидублирование: такой ученик уже есть (у любого куратора)?
    dup = await crud.find_student_duplicate(
        call.from_user.id, data.get("username"), data.get("user_id"),
        data.get("first"), data.get("last"))
    if dup:
        dg = await crud.get_group(dup.group_id)
        await state.clear()
        if dup.curator_id == call.from_user.id:
            await call.message.answer(
                f"⚠️ Ты уже добавлял(а) этого ученика: {dup.first_name} {dup.last_name} — "
                f"он в твоей группе «{dg.name if dg else '—'}».\n"
                "Повторно добавлять не нужно, иначе появятся двойники.\n"
                "Если хочешь перенести его в другую группу — сначала удали из старой "
                "(👥 Мои ученики → ➖ Удалить), потом добавь заново.")
        else:
            who = await crud.curator_label(dup.curator_id)
            await call.message.answer(
                f"⚠️ Этого ученика ({dup.first_name} {dup.last_name}) уже добавил "
                f"куратор {who} — в группу «{dg.name if dg else '—'}».\n"
                "Один ученик не может быть у двух кураторов одновременно.\n"
                "Если он должен быть у тебя — попроси того куратора удалить его, "
                "либо свяжись с администратором.")
        return

    st = await crud.add_student(
        first_name=data["first"], last_name=data["last"], username=data.get("username"),
        user_id=data.get("user_id"), group_id=gid, curator_id=call.from_user.id,
    )
    uname = f" (@{st.username})" if st.username else (f" ({st.user_id})" if st.user_id else "")

    # авто-приветствие: если знаем Telegram ID — пишем сразу
    delivered = await notifications.send_student_welcome(bot, st)
    if delivered:
        note = "📨 Ученику отправлено приветствие и видео-инструкция."
    else:
        note = ("❗ Передай ученику: пусть откроет бота и нажмёт /start (по тому же @username) — "
                "тогда он получит приветствие и видео-инструкцию. Бот не может написать первым.")
    await call.message.answer(
        f"✅ {st.first_name} {st.last_name}{uname} добавлен в группу «{g.name}».\n\n{note}")
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
    fresh, skipped = [], []
    for p in items:
        dup = await crud.find_student_duplicate(
            call.from_user.id, p.get("username"), p.get("user_id"),
            p.get("first"), p.get("last"))
        if dup:
            dg = await crud.get_group(dup.group_id)
            gname = dg.name if dg else "—"
            if dup.curator_id == call.from_user.id:
                skipped.append(f"{p['first']} {p['last']} — уже у тебя в «{gname}»")
            else:
                who = await crud.curator_label(dup.curator_id)
                skipped.append(f"{p['first']} {p['last']} — уже у куратора {who} в «{gname}»")
        else:
            fresh.append(p)
    n = await crud.add_students_bulk(fresh, group_id=gid, curator_id=call.from_user.id) if fresh else 0
    total = await crud.count_students(gid)
    lines = [f"✅ Добавлено {n} учеников в «{g.name}»."]
    for p in fresh[:15]:
        tail = f"(@{p['username']})" if p.get("username") else (f"({p['user_id']})" if p.get("user_id") else "")
        lines.append(f"   ✅ {p['first']} {p['last']} {tail}")
    if n > 15:
        lines.append(f"   … и ещё {n - 15}")
    if skipped:
        lines.append(f"\n⚠️ Пропущено {len(skipped)} — уже добавлены раньше (антидублирование):")
        for s_ in skipped[:12]:
            lines.append(f"   • {s_}")
        if len(skipped) > 12:
            lines.append(f"   … и ещё {len(skipped) - 12}")
    lines.append(f"\nВсего в группе: {total} учеников.")
    lines.append("\n❗ Каждый ученик должен сам открыть бота и нажать /start "
                 "(по тому же @username), тогда бот его узнает. Написать им первым бот не может.")
    await call.message.answer("\n".join(lines)[:4000])
    await state.clear()


# ─── ПОИСК УЧЕНИКА ──────────────────────────────────────────────

@router.message(F.text == "🔍 Найти ученика")
async def search_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(CuratorStates.search)
    await message.answer(
        "🔍 Поиск ученика.\n"
        "Напиши имя или фамилию (можно только часть, например «Асан») — \n"
        "я найду ученика и покажу его работы 👇")


@router.message(CuratorStates.search, F.text)
async def search_do(message: Message, state: FSMContext):
    await state.clear()
    found = await crud.search_students(message.from_user.id, message.text)
    if not found:
        await message.answer("Никого не нашёл. Попробуй другое имя.")
        return
    lines = [f"🔍 Найдено {len(found)}:"]
    rows = []
    for st in found:
        grp = await crud.get_group(st.group_id)
        uname = f" (@{st.username})" if st.username else ""
        lines.append(f"   • {st.first_name} {st.last_name}{uname} — {grp.name if grp else '—'}")
        rows.append([InlineKeyboardButton(
            text=f"📂 Работы {st.first_name} {st.last_name}", callback_data=f"cur_stwork:{st.id}")])
    await message.answer("\n".join(lines)[:4000],
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("cur_stwork:"))
async def student_works(call: CallbackQuery):
    await call.answer()
    sid = int(call.data.split(":")[1])
    st = await crud.get_student(sid)
    if not st or st.curator_id != call.from_user.id:
        await call.message.answer("❌ Ученик не найден.")
        return
    subs = await crud.get_submissions(student_id=sid, curator_id=call.from_user.id)
    if not subs:
        await call.message.answer(f"👤 {st.first_name} {st.last_name}\nРабот пока нет.")
        return
    lines = [f"👤 {st.first_name} {st.last_name} — работы:"]
    rows = []
    for i, sub in enumerate(subs, start=1):
        mark = " ⚠️" if sub.is_late else ""
        lines.append(f"   📤 #{i} — {roles.fmt_absolute(sub.submitted_at_utc)}{mark}")
        rows.append([InlineKeyboardButton(text=f"📄 Открыть #{i}", callback_data=f"open_sub:{sub.id}")])
    await call.message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


# ─── РАССЫЛКА (объявление ученикам) ─────────────────────────────

@router.message(F.text == "📢 Рассылка")
async def broadcast_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(CuratorStates.broadcast)
    await message.answer(
        "📢 Рассылка — это объявление сразу всем твоим ученикам.\n"
        "Например: «Дедлайн перенесён на субботу».\n\n"
        "Напиши текст одним сообщением — и я разошлю его всем, кто уже запускал бота.\n"
        "Передумал(а)? Напиши /cancel.")


@router.message(CuratorStates.broadcast, F.text)
async def broadcast_do(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    text = message.text
    targets = await crud.broadcast_targets(curator_id=message.from_user.id)
    if not targets:
        await message.answer("Пока некому отправлять — ученики ещё не запускали бота.")
        return
    sent = 0
    for uid in targets:
        try:
            await bot.send_message(uid, f"📢 Сообщение от куратора:\n\n{text}")
            sent += 1
        except Exception:
            pass
    await message.answer(f"✅ Объявление отправлено: {sent} из {len(targets)} учеников.")


@router.callback_query(F.data.startswith("cur_export:"))
async def export_group(call: CallbackQuery, bot: Bot):
    await call.answer()
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    if not g or g.curator_id != call.from_user.id:
        await call.message.answer("❌ Группа не найдена.")
        return
    subs = await crud.get_submissions(group_id=gid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Только Excel-сводку", callback_data=f"cur_exxls:{gid}")],
        [InlineKeyboardButton(text=f"📄 Excel + все PDF ({len(subs)} файлов)",
                              callback_data=f"cur_exall:{gid}")],
    ])
    await call.message.answer(
        f"📦 Выгрузка группы «{g.name}» — {len(subs)} сдач.\n\n"
        "Файлы придут тебе в этот чат, дальше можешь переслать в любой другой чат.\n"
        "Выбери формат:",
        reply_markup=kb)


@router.callback_query(F.data.startswith("cur_exxls:"))
async def export_group_xls(call: CallbackQuery, bot: Bot):
    await call.answer("Готовлю Excel…")
    from aiogram.types import BufferedInputFile
    from services import section_export
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    raw = await section_export.group_xlsx(gid)
    await bot.send_document(call.from_user.id,
                            BufferedInputFile(raw, f"{g.name}_сводка.xlsx".replace(" ", "_")),
                            caption=f"📊 Сводка группы «{g.name}» — листы по разделам.")


@router.callback_query(F.data.startswith("cur_exall:"))
async def export_group_all(call: CallbackQuery, bot: Bot):
    await call.answer("Выгружаю…")
    from aiogram.types import BufferedInputFile
    from services import section_export
    gid = int(call.data.split(":")[1])
    g = await crud.get_group(gid)
    raw = await section_export.group_xlsx(gid)
    await bot.send_document(call.from_user.id,
                            BufferedInputFile(raw, f"{g.name}_сводка.xlsx".replace(" ", "_")),
                            caption=f"📊 Сводка «{g.name}». Ниже — все PDF.")
    subs = await crud.get_submissions(group_id=gid)
    sent = 0
    for sub in subs:
        st = await crud.get_student(sub.student_id)
        sec = await crud.get_section(sub.section_id) if sub.section_id else None
        name = f"{st.first_name} {st.last_name}".strip() if st else "—"
        t = "практика" if sub.type == "practice" else "конспект"
        cap = f"{name} · {t}\n{sec.name if sec else '—'} · Неделя {sub.week} · {sub.submitted_name}"
        try:
            await bot.send_document(call.from_user.id, sub.pdf_file_id, caption=cap)
            sent += 1
        except Exception:
            pass
    await call.message.answer(f"✅ Выгружено {sent} PDF. Можешь переслать их в нужный чат.")


# ─── КТО СДАЛ (РТ и практика — раздельно) ──────────────────────

@router.message(F.text == "📥 Кто сдал РТ")
async def who_rt_pick(message: Message, state: FSMContext):
    await state.clear()
    groups = await crud.get_groups(curator_id=message.from_user.id)
    if not groups:
        await message.answer("У тебя пока нет групп.")
        return
    await message.answer("📄 Конспекты (РТ) — выбери группу:",
                         reply_markup=groups_inline(groups, "cur_whoW"))


@router.message(F.text == "📷 Кто сдал практику")
async def who_pr_pick(message: Message, state: FSMContext):
    await state.clear()
    groups = await crud.get_groups(curator_id=message.from_user.id)
    if not groups:
        await message.answer("У тебя пока нет групп.")
        return
    await message.answer("📷 Практика — выбери группу:",
                         reply_markup=groups_inline(groups, "cur_whoP"))


@router.callback_query(F.data.startswith("cur_whoW:"))
async def who_sections_w(call: CallbackQuery):
    await call.answer()
    await _section_list(call, int(call.data.split(":")[1]), "workbook")


@router.callback_query(F.data.startswith("cur_whoP:"))
async def who_sections_p(call: CallbackQuery):
    await call.answer()
    await _section_list(call, int(call.data.split(":")[1]), "practice")


async def _section_list(call: CallbackQuery, gid: int, sub_type: str):
    g = await crud.get_group(gid)
    if not g or g.curator_id != call.from_user.id:
        await call.message.answer("❌ Группа не найдена.")
        return
    sections = await crud.get_sections(gid)
    kind = "📷 Практика" if sub_type == "practice" else "📄 Конспекты (РТ)"
    rows = []
    for sec in sections:
        mark = "" if sec.is_active else " (завершён)"
        rows.append([InlineKeyboardButton(
            text=f"📚 {sec.name}{mark}", callback_data=f"cur_secW:{sec.id}:{sub_type}")])
    rows.append([InlineKeyboardButton(text="➕ Добавить раздел", callback_data=f"cur_addsec:{gid}")])
    txt = (f"{kind} · группа «{g.name}»\nВыбери раздел:" if sections else
           f"{kind} · группа «{g.name}»\n\nРазделов пока нет. Создай первый 👇")
    await call.message.answer(txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("cur_secW:"))
async def section_open(call: CallbackQuery):
    await call.answer()
    _, sid, t = call.data.split(":")
    await _show_week(call, int(sid), 1, t)


@router.callback_query(F.data.startswith("cur_wk:"))
async def section_week_nav(call: CallbackQuery):
    await call.answer()
    _, sid, w, t = call.data.split(":")
    await _show_week(call, int(sid), int(w), t)


async def _show_week(call: CallbackQuery, sid: int, week: int, sub_type: str):
    sec = await crud.get_section(sid)
    if not sec or sec.curator_id != call.from_user.id:
        await call.message.answer("❌ Раздел не найден.")
        return
    g = await crud.get_group(sec.group_id)
    students = await crud.get_students(curator_id=call.from_user.id, group_id=sec.group_id)
    subs = await crud.get_submissions(curator_id=call.from_user.id, section_id=sid,
                                      week=week, sub_type=sub_type)
    last = {}
    for sub in subs:
        last.setdefault(sub.student_id, sub)

    is_prac = sub_type == "practice"
    kind = "📷 Практика" if is_prac else "📄 Конспекты (РТ)"
    icon = "📷" if is_prac else "📄"
    total = (max(1, getattr(sec, "practices", 2) or 2)) if is_prac else sec.weeks
    wk_word = f"Практика {week} из {total}" if is_prac else f"Неделя {week} из {total}"
    lines = [f"{kind} · {sec.name} · {wk_word}", f"Группа «{g.name}»\n"]
    pdf_rows = []
    done = 0
    # students уже отсортированы по фамилии+имени (А→Я)
    for st in students:
        fio = f"{st.last_name} {st.first_name}".strip()
        sub = last.get(st.id)
        if sub:
            done += 1
            if sub.is_late:
                lines.append(f"   ⚠️ {fio} · ПРОСРОЧЕНО +{roles.fmt_late(sub.late_by_minutes)} · "
                             f"сдал(а) {roles.fmt_relative(sub.submitted_at_utc)}")
            else:
                lines.append(f"   ✅ {fio} · сдал(а) {roles.fmt_relative(sub.submitted_at_utc)}")
            pdf_rows.append(InlineKeyboardButton(
                text=f"{icon} {st.first_name}" + (" ⚠️" if sub.is_late else ""),
                callback_data=f"open_sub:{sub.id}"))
        else:
            lines.append(f"   ❌ {fio} · не сдал(а)")
    lines.append(f"\nСдали: {done} из {len(students)} | Не сдали: {len(students) - done}")

    rows = [pdf_rows[j:j+2] for j in range(0, len(pdf_rows), 2)]
    nav = []
    for w in range(1, total + 1):
        prefix = "Практика " if is_prac else ""
        label = f"·{prefix}{w}·" if w == week else f"{prefix}{w}"
        nav.append(InlineKeyboardButton(text=label, callback_data=f"cur_wk:{sid}:{w}:{sub_type}"))
    rows += [nav[j:j+5] for j in range(0, len(nav), 5)]
    rows.append([InlineKeyboardButton(text="📊 Excel раздела", callback_data=f"cur_secxls:{sid}"),
                 InlineKeyboardButton(text="🗑 Удалить раздел", callback_data=f"cur_delsec:{sid}")])
    await call.message.answer("\n".join(lines)[:4000],
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("cur_delsec:"))
async def del_section_confirm(call: CallbackQuery):
    await call.answer()
    sid = int(call.data.split(":")[1])
    sec = await crud.get_section(sid)
    if not sec or sec.curator_id != call.from_user.id:
        await call.message.answer("❌ Раздел не найден.")
        return
    subs = await crud.get_submissions(section_id=sid)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"cur_delsec_yes:{sid}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cur_cancel"),
    ]])
    await call.message.answer(
        f"🗑 Удалить раздел «{sec.name}»?\n"
        f"Вместе с ним удалятся {len(subs)} сдач этого раздела.\n"
        "Используй, если создал раздел по ошибке.",
        reply_markup=kb)


@router.callback_query(F.data.startswith("cur_delsec_yes:"))
async def del_section_do(call: CallbackQuery):
    await call.answer()
    sid = int(call.data.split(":")[1])
    sec = await crud.get_section(sid)
    if not sec or sec.curator_id != call.from_user.id:
        await call.message.answer("❌ Раздел не найден.")
        return
    n = await crud.delete_section(sid, by=call.from_user.id)
    await call.message.answer(f"✅ Раздел «{sec.name}» удалён (убрано {n} сдач).")


# ── создание раздела ──
@router.callback_query(F.data.startswith("cur_addsec:"))
async def add_section_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    gid = int(call.data.split(":")[1])
    await state.set_state(CuratorStates.new_section_name)
    await state.update_data(sec_gid=gid)
    await call.message.answer(
        "📚 Создаём раздел.\n"
        "Раздел — это предмет, который сейчас проходят ученики (например Анатомия).\n"
        "Внутри раздела они будут сдавать конспекты по неделям и практику.\n\n"
        "Напиши название раздела одним сообщением 👇")


@router.message(CuratorStates.new_section_name, F.text)
async def add_section_name(message: Message, state: FSMContext):
    await state.update_data(sec_name=message.text.strip())
    await state.set_state(CuratorStates.new_section_period)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="7 дней (1 неделя)", callback_data="cur_secper:7"),
        InlineKeyboardButton(text="14 дней (2 недели)", callback_data="cur_secper:14"),
    ]])
    await message.answer(
        f"\u2705 Название принято: «{message.text.strip()}»\n\n"
        "\U0001f4d8 <b>ШАГ 2 из 4 — длительность одной недели</b>\n\n"
        "Раздел делится на «недели» (периоды). Сколько дней длится ОДНА такая неделя?\n\n"
        "Пример: «Ботаника 1» идёт 7 дней, потом «Ботаника 2» — ещё 7 дней, и так далее.\n"
        "Или каждая тема длится 14 дней (2 недели).\n\n"
        "Нажми на кнопку 👇",
        parse_mode="HTML", reply_markup=kb)


def _local_midnight(d) -> datetime:
    """date (локальная) → datetime в UTC, соответствующий локальной полуночи."""
    local = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=TZ_ASTANA)
    return local.astimezone(timezone.utc)


@router.callback_query(CuratorStates.new_section_period, F.data.startswith("cur_secper:"))
async def add_section_period(call: CallbackQuery, state: FSMContext):
    await call.answer()
    period = int(call.data.split(":")[1])
    await state.update_data(sec_period=period)
    await state.set_state(CuratorStates.new_section_count)
    unit = "недель" if period == 7 else "периодов по 2 недели"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="4 недели (≈ месяц)", callback_data="cur_seccnt:4"),
         InlineKeyboardButton(text="2 недели", callback_data="cur_seccnt:2")],
        [InlineKeyboardButton(text="✍️ Указать другое число", callback_data="cur_seccnt:manual")],
    ])
    await call.message.answer(
        f"\u2705 Одна неделя = {period} дней.\n\n"
        "\U0001f4d8 <b>ШАГ 3 из 4 — сколько всего недель идёт раздел?</b>\n\n"
        "Это весь срок предмета. Например, если раздел длится месяц — это 4 недели "
        "(Ботаника 1, 2, 3, 4).\n\n"
        "Выбери кнопкой или нажми «Указать другое число» и напиши, например: 6 👇",
        parse_mode="HTML", reply_markup=kb)


@router.callback_query(CuratorStates.new_section_count, F.data.startswith("cur_seccnt:"))
async def add_section_count(call: CallbackQuery, state: FSMContext):
    await call.answer()
    val = call.data.split(":")[1]
    if val == "manual":
        await call.message.answer(
            "✍️ Напиши число — сколько всего недель идёт раздел (например 6):")
        return
    await state.update_data(sec_weeks=int(val))
    await _ask_section_start(call.message, state)


@router.message(CuratorStates.new_section_count, F.text.regexp(r"^\d{1,2}$"))
async def add_section_count_text(message: Message, state: FSMContext):
    weeks = max(1, min(30, int(message.text)))
    await state.update_data(sec_weeks=weeks)
    await _ask_section_start(message, state)


@router.message(CuratorStates.new_section_count, F.text)
async def add_section_count_bad(message: Message, state: FSMContext):
    await message.answer("❌ Нужно просто число, например 4. Сколько недель идёт раздел?")


async def _ask_section_start(target, state: FSMContext):
    await state.set_state(CuratorStates.new_section_start)
    data = await state.get_data()
    weeks = data.get("sec_weeks", 4)
    period = data.get("sec_period", 7)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📆 Сегодня", callback_data="cur_secstart:today"),
         InlineKeyboardButton(text="📆 Завтра", callback_data="cur_secstart:tomorrow")],
        [InlineKeyboardButton(text="📆 С ближайшего понедельника", callback_data="cur_secstart:monday")],
    ])
    await target.answer(
        f"\u2705 Раздел будет идти {weeks} нед. по {period} дн.\n\n"
        "\U0001f4d8 <b>ШАГ 4 из 4 — с какого числа стартует раздел?</b>\n\n"
        "От этого дня пойдёт отсчёт. Неделя 1 начнётся в этот день, через "
        f"{period} дн. она закроется и начнётся неделя 2, и так далее.\n"
        "Конечную дату бот посчитает сам.\n\n"
        "Выбери кнопкой или напиши дату: <b>ДД.ММ.ГГГГ</b> (например 01.06.2025) 👇",
        parse_mode="HTML", reply_markup=kb)


@router.callback_query(CuratorStates.new_section_start, F.data.startswith("cur_secstart:"))
async def add_section_start_btn(call: CallbackQuery, state: FSMContext):
    await call.answer()
    which = call.data.split(":")[1]
    today = roles.now_local().date()
    if which == "today":
        d = today
    elif which == "tomorrow":
        d = today + timedelta(days=1)
    else:
        d = today + timedelta(days=(0 - today.weekday()) % 7)
    await _finish_create_section(call.message, state, call.from_user.id, d)


@router.message(CuratorStates.new_section_start, F.text)
async def add_section_start_text(message: Message, state: FSMContext):
    d = _parse_date(message.text)
    if not d:
        await message.answer("❌ Не понял дату. Напиши ДД.ММ.ГГГГ, например 01.06.2025.")
        return
    await _finish_create_section(message, state, message.from_user.id, d)


async def _finish_create_section(target, state: FSMContext, curator_id: int, start_d):
    data = await state.get_data()
    gid = data["sec_gid"]
    period = data.get("sec_period", 7)
    weeks = data.get("sec_weeks", 4)
    name = data.get("sec_name", "Раздел")
    start_dt = _local_midnight(start_d)
    # конец = старт + weeks*period дней − 1 день, 23:59 локально
    end_local_date = start_d + timedelta(days=period * weeks - 1)
    end_dt = _local_midnight(end_local_date).replace(hour=23, minute=59)
    sec = await crud.create_section(gid, curator_id, name, weeks,
                                     start_date=start_dt, end_date=end_dt,
                                     period_days=period)
    await state.clear()
    g = await crud.get_group(gid)
    total = roles.section_total_weeks(sec)
    lines = [
        f"\u2705 Раздел «{sec.name}» создан в группе «{g.name}»!",
        "",
        f"\U0001f4c6 Начало: {roles.section_start_str(sec)}",
        f"\U0001f3c1 Конец: {roles.section_end_str(sec)}",
        f"\u23f1 Одна неделя = {period} дн. · Всего недель: {total}",
        "",
        "\U0001f5d3 <b>Расписание недель:</b>",
    ]
    for w in range(1, total + 1):
        lines.append(f"   • Неделя {w}: {roles.section_week_range_str(sec, w)} → дедлайн {roles.section_deadline_str(sec, w)}")
    lines += [
        "",
        "Как это работает для учеников:",
        "• сейчас идёт только текущая неделя — её сдают вовремя;",
        "• следующая неделя откроется, только когда закончится текущая;",
        "• за прошлую неделю можно сдать, но бот пометит как просрочку.",
    ]
    await target.answer("\n".join(lines), parse_mode="HTML")


# ── Excel по разделу ──
@router.callback_query(F.data.startswith("cur_secxls:"))
async def section_excel(call: CallbackQuery, bot: Bot):
    await call.answer("Готовлю Excel…")
    from aiogram.types import BufferedInputFile
    from services import section_export
    sid = int(call.data.split(":")[1])
    sec = await crud.get_section(sid)
    if not sec or sec.curator_id != call.from_user.id:
        await call.message.answer("❌ Раздел не найден.")
        return
    raw = await section_export.section_xlsx(sid)
    g = await crud.get_group(sec.group_id)
    fname = f"{g.name}_{sec.name}.xlsx".replace(" ", "_")
    await bot.send_document(call.from_user.id, BufferedInputFile(raw, fname),
                            caption=f"📊 {g.name} — {sec.name}: сдачи по неделям.")


@router.callback_query(F.data.startswith("open_sub:"))
async def open_submission(call: CallbackQuery):
    await call.answer()
    sub_id = int(call.data.split(":")[1])
    sub = await crud.get_submission(sub_id)
    if not sub or sub.curator_id != call.from_user.id:
        await call.message.answer("❌ Файл не найден.")
        return
    st = await crud.get_student(sub.student_id)
    realname = f"{st.first_name} {st.last_name}".strip() if st else "—"
    sec = await crud.get_section(sub.section_id) if sub.section_id else None
    kind = "📷 практика" if sub.type == "practice" else "📄 конспект"
    sec_line = f"\n{kind} · {sec.name} · Неделя {sub.week}" if sec else f"\n{kind}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Удалить работу (разрешить пересдачу)",
                             callback_data=f"cur_purge:{sub.id}")
    ]])
    await call.message.answer_document(
        sub.pdf_file_id,
        caption=f"👤 {realname}{sec_line}\n📄 Файл: {sub.submitted_name}\n"
                f"📤 {roles.fmt_absolute(sub.submitted_at_utc)}",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("cur_purge:"))
async def purge_submission(call: CallbackQuery, bot: Bot):
    await call.answer()
    sub_id = int(call.data.split(":")[1])
    sub = await crud.get_submission(sub_id)
    if not sub or sub.curator_id != call.from_user.id:
        await call.message.answer("❌ Работа не найдена.")
        return
    st = await crud.get_student(sub.student_id)
    sec = await crud.get_section(sub.section_id) if sub.section_id else None
    await crud.soft_delete_submission(sub_id, by=call.from_user.id)
    what = "практику" if sub.type == "practice" else "конспект"
    await call.message.answer(
        f"✅ Работа удалена. Теперь ученик может пересдать {what} за «"
        f"{sec.name if sec else '—'}», неделя {sub.week}.")
    # уведомим ученика
    if st and st.user_id:
        try:
            await bot.send_message(
                st.user_id,
                f"♻️ Куратор удалил твою работу ({what}) по «{sec.name if sec else '—'}», "
                f"неделя {sub.week}. Можешь сдать заново.")
        except Exception:
            pass


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

