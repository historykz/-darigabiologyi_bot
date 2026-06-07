"""Хэндлеры администратора: кураторы, тетради, все данные, бэкап, восстановление, Sheets."""
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import TZ
from database import crud
from handlers.filters import RoleFilter
from keyboards.admin_kb import (
    admin_menu,
    backup_files,
    backup_menu,
    curators_menu,
)
from services import backup, restore, roles
from states.fsm_states import AdminStates

router = Router()
router.message.filter(RoleFilter("admin"))
router.callback_query.filter(RoleFilter("admin"))


# ─── КУРАТОРЫ ───────────────────────────────────────────────────

@router.message(F.text == "👨‍🏫 Кураторы")
async def curators(message: Message, state: FSMContext):
    await state.clear()
    cur = await crud.get_curators()
    lines = ["👨‍🏫 Кураторы:"]
    if cur:
        for i, c in enumerate(cur, start=1):
            uname = f" (@{c.username})" if c.username else ""
            lines.append(f"   {i}. {c.first_name or ''} {c.last_name or ''}{uname}".rstrip())
    else:
        lines.append("   (пока нет кураторов)")
    await message.answer("\n".join(lines), reply_markup=curators_menu())


@router.callback_query(F.data == "adm_add_curator")
async def add_curator_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminStates.add_curator)
    await call.message.answer("Введи @username или Telegram ID пользователя, которого назначить куратором:")


@router.message(AdminStates.add_curator, F.text)
async def add_curator(message: Message, state: FSMContext, bot: Bot):
    contact = message.text.strip()
    await state.clear()
    if contact.isdigit():
        tg_id = int(contact)
        await crud.upsert_user(tg_id, None, None, None, role="curator")
        try:
            await bot.send_message(tg_id, "👨‍🏫 Вам назначена роль Куратора. Напишите /start чтобы начать.")
        except Exception:
            pass
        await message.answer(f"✅ Пользователю {tg_id} назначена роль Куратора.", reply_markup=admin_menu())
    elif contact.startswith("@"):
        # без user_id мы не можем написать ему сами — запомним по username,
        # роль присвоится при первом /start этого username
        uname = contact[1:]
        await crud.set_setting(f"pending_curator:{uname.lower()}", "1")
        await message.answer(
            f"✅ @{uname} помечен как будущий куратор.\n"
            "Как только он напишет боту /start — роль активируется автоматически.",
            reply_markup=admin_menu(),
        )
    else:
        await message.answer("⚠️ Нужен @username или числовой Telegram ID.")


@router.callback_query(F.data == "adm_del_curator")
async def del_curator_list(call: CallbackQuery):
    await call.answer()
    cur = await crud.get_curators()
    if not cur:
        await call.message.answer("Нет активных кураторов.")
        return
    rows = [[InlineKeyboardButton(
        text=f"{c.first_name or c.username or c.telegram_id}",
        callback_data=f"adm_delcur:{c.telegram_id}")] for c in cur]
    await call.message.answer("Кого удалить?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("adm_delcur:"))
async def del_curator_confirm(call: CallbackQuery):
    await call.answer()
    tg_id = int(call.data.split(":")[1])
    user = await crud.get_user_by_tg(tg_id)
    name = (user.first_name if user else None) or str(tg_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да", callback_data=f"adm_delcuryes:{tg_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="adm_cancel"),
    ]])
    await call.message.answer(f"Удалить куратора {name}? Его группы останутся в архиве.",
                              reply_markup=kb)


@router.callback_query(F.data.startswith("adm_delcuryes:"))
async def del_curator_do(call: CallbackQuery):
    await call.answer()
    tg_id = int(call.data.split(":")[1])
    await crud.set_role(tg_id, "student")
    await call.message.answer("✅ Роль куратора снята. Группы сохранены в архиве.")


@router.callback_query(F.data == "adm_cancel")
async def adm_cancel(call: CallbackQuery):
    await call.answer("Отменено")
    await call.message.answer("❌ Отменено.")


# ─── РАБОЧИЕ ТЕТРАДИ (загрузка) ─────────────────────────────────

@router.message(F.text == "📚 Рабочие тетради")
async def workbooks(message: Message, state: FSMContext):
    await state.clear()
    wbs = await crud.get_workbooks()
    rows = []
    if wbs:
        for w in wbs:
            rows.append([
                InlineKeyboardButton(text=f"📄 №{w.serial:03d} — {w.topic}",
                                     callback_data=f"wb_send:{w.id}"),
                InlineKeyboardButton(text="🗑", callback_data=f"adm_delwb:{w.id}"),
            ])
    head = "📚 Рабочие тетради:\nНажми 📄 — пришлю PDF, 🗑 — удалить.\n\nЧтобы загрузить новую — пришли PDF-файл 👇"
    if not wbs:
        head = "📚 Рабочих тетрадей пока нет.\nЧтобы загрузить — пришли PDF-файл 👇"
    kb = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    await message.answer(head, reply_markup=kb)
    await state.set_state(AdminStates.workbook_wait_pdf)


@router.callback_query(F.data.startswith("wb_send:"))
async def admin_send_workbook(call: CallbackQuery):
    await call.answer()
    wb_id = int(call.data.split(":")[1])
    wb = await crud.get_workbook(wb_id)
    if not wb:
        await call.message.answer("❌ Эта тетрадь больше недоступна.")
        return
    await call.message.answer_document(wb.file_id, caption=f"📄 №{wb.serial:03d} — {wb.topic}")


@router.callback_query(F.data.startswith("adm_delwb:"))
async def workbook_del_confirm(call: CallbackQuery):
    await call.answer()
    wb_id = int(call.data.split(":")[1])
    wb = await crud.get_workbook(wb_id)
    if not wb:
        await call.message.answer("❌ Тетрадь не найдена.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"adm_delwbyes:{wb_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="adm_cancel"),
    ]])
    await call.message.answer(
        f"🗑 Удалить рабочую тетрадь №{wb.serial:03d} — «{wb.topic}»?\n"
        "Она пропадёт из списка у всех учеников.",
        reply_markup=kb)


@router.callback_query(F.data.startswith("adm_delwbyes:"))
async def workbook_del_do(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    wb_id = int(call.data.split(":")[1])
    wb = await crud.get_workbook(wb_id)
    serial = wb.serial if wb else wb_id
    ok = await crud.delete_workbook(wb_id)
    if ok:
        await call.message.answer(f"✅ Рабочая тетрадь №{serial:03d} удалена.")
    else:
        await call.message.answer("❌ Не удалось удалить — возможно, уже удалена.")


@router.message(AdminStates.workbook_wait_pdf, F.document)
async def workbook_pdf(message: Message, state: FSMContext):
    doc = message.document
    if not (doc.mime_type == "application/pdf" or (doc.file_name or "").lower().endswith(".pdf")):
        await message.answer("⚠️ Нужен именно PDF-файл.")
        return
    await state.update_data(file_id=doc.file_id)
    await state.set_state(AdminStates.workbook_wait_topic)
    await message.answer("Введи название темы рабочей тетради:")


@router.message(AdminStates.workbook_wait_topic, F.text)
async def workbook_topic(message: Message, state: FSMContext):
    data = await state.get_data()
    serial = await crud.next_workbook_serial()
    wb = await crud.add_workbook(serial, message.text.strip(), data["file_id"])
    await state.clear()
    await message.answer(
        f"✅ Рабочая тетрадь №{wb.serial:03d} — «{wb.topic}» сохранена.\n"
        f"Доступна всем ученикам по серийному номеру №{wb.serial:03d}.",
        reply_markup=admin_menu(),
    )


# ─── ВСЕ УЧЕНИКИ ────────────────────────────────────────────────

@router.message(F.text == "👥 Все ученики")
async def all_students(message: Message, state: FSMContext):
    await state.clear()
    groups = await crud.get_groups()
    if not groups:
        await message.answer("Учеников пока нет.")
        return
    lines = ["👥 Все ученики системы:\n"]
    for g in groups:
        cur = await crud.get_user_by_tg(g.curator_id)
        cur_name = (cur.first_name if cur and cur.first_name else str(g.curator_id))
        sts = await crud.get_students(group_id=g.id)
        lines.append(f"📂 {g.name} · куратор {cur_name} ({len(sts)} чел.)")
        for st in sts:
            uname = f" (@{st.username})" if st.username else ""
            lines.append(f"   • {st.first_name} {st.last_name}{uname}")
        lines.append("")
    await message.answer("\n".join(lines)[:4000])


# ─── ВСЕ ОТЧЁТЫ ─────────────────────────────────────────────────

@router.message(F.text == "📊 Все отчёты")
async def all_reports(message: Message, state: FSMContext):
    await state.clear()
    stats = await crud.global_stats()
    groups = await crud.get_groups()
    lines = [
        "📊 Сводная статистика\n",
        f"👨‍🏫 Кураторов: {stats['curators']}",
        f"👥 Учеников: {stats['students']}",
        f"📂 Групп: {stats['groups']}",
        f"📤 Работ сдано: {stats['submissions']}",
        f"📚 Тетрадей: {stats['workbooks']}",
        "\nПо группам:",
    ]
    for g in groups:
        sts = await crud.get_students(group_id=g.id)
        subs = await crud.get_submissions(group_id=g.id)
        submitted_ids = {s.student_id for s in subs}
        done = len([s for s in sts if s.id in submitted_ids])
        lines.append(f"   📂 {g.name}: сдали {done} из {len(sts)}")
    await message.answer("\n".join(lines))


# ─── РЕЗЕРВНАЯ КОПИЯ ────────────────────────────────────────────

@router.message(F.text == "💾 Резервная копия")
async def backup_root(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("💾 Резервная копия", reply_markup=backup_menu())


@router.callback_query(F.data == "adm_backup_now")
async def backup_now(call: CallbackQuery):
    await call.answer()
    await call.message.answer("⏳ Создаю резервную копию...")
    data = await backup.collect()
    stats = await crud.global_stats()
    text = (
        "Собираю данные:\n"
        f"✅ Пользователи: {stats['curators']} кураторов, {stats['students']} учеников\n"
        f"✅ Групп: {stats['groups']}\n"
        f"✅ Работ сдано: {stats['submissions']}\n"
        f"✅ Рабочих тетрадей: {stats['workbooks']}\n\n"
        f"Готово! Файл: backup_{datetime.now(TZ):%d.%m.%Y}.json"
    )
    await call.message.answer(text, reply_markup=backup_files())


@router.callback_query(F.data == "adm_bk_json")
async def backup_json(call: CallbackQuery, bot: Bot):
    await call.answer()
    data = await backup.collect()
    raw = backup.make_json(data)
    fname = f"backup_{datetime.now(TZ):%d.%m.%Y}.json"
    await bot.send_document(call.from_user.id, BufferedInputFile(raw, fname))


@router.callback_query(F.data == "adm_bk_xlsx")
async def backup_xlsx(call: CallbackQuery, bot: Bot):
    await call.answer()
    data = await backup.collect()
    raw = backup.make_excel(data)
    fname = f"backup_{datetime.now(TZ):%d.%m.%Y}.xlsx"
    await bot.send_document(call.from_user.id, BufferedInputFile(raw, fname))


@router.callback_query(F.data == "adm_restore")
async def restore_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminStates.restore_wait_file)
    await call.message.answer(
        "🔄 Восстановление из резервной копии\n\n"
        "⚠️ После обновления бота загрузи бэкап — все данные восстановятся автоматически.\n"
        "Кураторам и ученикам ничего делать не нужно.\n\n"
        "Отправь файл backup_*.json 👇"
    )


@router.message(AdminStates.restore_wait_file, F.document)
async def restore_file(message: Message, state: FSMContext, bot: Bot):
    doc = message.document
    if not (doc.file_name or "").lower().endswith(".json"):
        await message.answer("⚠️ Нужен JSON-файл backup_*.json.")
        return
    f = await bot.get_file(doc.file_id)
    buf = await bot.download_file(f.file_path)
    try:
        data = restore.parse(buf.read())
    except Exception:
        await message.answer("❌ Не удалось прочитать файл. Это точно backup_*.json?")
        return
    summary = restore.summarize(data)
    await state.update_data(restore_data=data)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Восстановить", callback_data="adm_restore_yes"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="adm_cancel"),
    ]])
    await message.answer(
        "📋 Найдено в файле:\n"
        f"   👨‍🏫 Кураторов: {summary['curators']}\n"
        f"   👥 Учеников: {summary['students']}\n"
        f"   📂 Групп: {summary['groups']}\n"
        f"   📤 Работ: {summary['submissions']}\n"
        f"   📚 Тетрадей: {summary['workbooks']}\n\n"
        "Восстановить всё?",
        reply_markup=kb,
    )


@router.callback_query(F.data == "adm_restore_yes")
async def restore_do(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = (await state.get_data()).get("restore_data")
    if not data:
        await call.message.answer("❌ Данные для восстановления потеряны, попробуй заново.")
        return
    counts = await restore.restore(data)
    await state.clear()
    await call.message.answer(
        "✅ Восстановление завершено!\n\n"
        f"✅ {counts['curators']} кураторов восстановлены\n"
        f"✅ {counts['students']} учеников восстановлены\n"
        f"✅ {counts['groups']} групп восстановлены\n"
        f"✅ {counts['submissions']} работ восстановлены\n\n"
        "Бот готов к работе. Кураторам и ученикам ничего делать не нужно — всё как было.",
        reply_markup=admin_menu(),
    )


# ─── GOOGLE SHEETS ──────────────────────────────────────────────

@router.message(F.text == "📊 Google Sheets")
async def gsheets(message: Message, state: FSMContext):
    await state.clear()
    current = await crud.get_setting("gsheet_url")
    cur_line = f"\nТекущая таблица: {current}" if current else ""
    await state.set_state(AdminStates.gsheet_wait_url)
    await message.answer(
        "📊 Google Sheets — живой журнал сдач.\n"
        "Пришли ссылку на таблицу (доступ должен быть выдан service-аккаунту)."
        f"{cur_line}"
    )


@router.message(AdminStates.gsheet_wait_url, F.text)
async def gsheets_save(message: Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        await message.answer("⚠️ Нужна ссылка на Google-таблицу.")
        return
    await crud.set_setting("gsheet_url", url)
    await state.clear()
    await message.answer("✅ Таблица подключена. Новые сдачи будут дублироваться туда.",
                         reply_markup=admin_menu())
