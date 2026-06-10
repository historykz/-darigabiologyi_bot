"""Хэндлеры ученика: рабочие тетради, сдача РТ, мои работы."""
import html
import re
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database import crud
from handlers.filters import RoleFilter
from keyboards.student_kb import send_pdf_kb, student_menu
from services import notifications, pdf_builder, roles, sheets
from states.fsm_states import StudentStates

router = Router()
router.message.filter(RoleFilter("student"))
router.callback_query.filter(RoleFilter("student"))


# ─── ВИДЕО-ИНСТРУКЦИЯ (онбординг) ───────────────────────────────

async def _intro_ok(uid: int, target) -> bool:
    """True, если ученику можно пользоваться ботом (видео просмотрено или его нет).

    uid — telegram_id ученика, target — объект с .answer()/.bot для ответа.
    """
    video = await crud.get_setting("intro_video")
    if not video:
        return True
    st = await crud.get_student_by_tg(uid)
    if st and st.intro_watched:
        return True
    if st:
        await notifications.send_student_welcome(target.bot, st)
    await target.answer("📺 Сначала посмотри видео-инструкцию выше и нажми «✅ Я посмотрел(а)».")
    return False


@router.callback_query(F.data == "intro_done")
async def intro_done(call: CallbackQuery):
    await call.answer("Готово!")
    await crud.set_intro_watched(call.from_user.id)
    await call.message.answer(
        "✅ Отлично! Теперь можешь пользоваться ботом:\n"
        "📚 смотреть рабочие тетради и 📤 сдавать РТ.",
        reply_markup=student_menu(),
    )


# ─── РАБОЧИЕ ТЕТРАДИ ────────────────────────────────────────────

@router.message(F.text == "📚 Рабочие тетради")
async def list_workbooks(message: Message, state: FSMContext):
    await state.clear()
    if not await _intro_ok(message.from_user.id, message):
        return
    wbs = await crud.get_workbooks()
    if not wbs:
        await message.answer("📚 Пока нет загруженных рабочих тетрадей.")
        return
    rows = [[InlineKeyboardButton(text=f"📄 №{w.serial:03d} — {w.topic}",
                                  callback_data=f"wb_send:{w.id}")] for w in wbs]
    await message.answer(
        "📚 Доступные рабочие тетради:\nНажми на нужную — пришлю PDF 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("wb_send:"))
async def send_workbook_btn(call: CallbackQuery):
    await call.answer()
    wb_id = int(call.data.split(":")[1])
    wb = await crud.get_workbook(wb_id)
    if not wb:
        await call.message.answer("❌ Эта тетрадь больше недоступна.")
        return
    await call.message.answer_document(wb.file_id, caption=f"📄 №{wb.serial:03d} — {wb.topic}")


# ─── СДАТЬ РТ ───────────────────────────────────────────────────

@router.message(F.text == "📤 Сдать РТ")
async def submit_start(message: Message, state: FSMContext):
    await state.clear()
    if not await _intro_ok(message.from_user.id, message):
        return
    await _begin_submit(message.from_user.id, message, state, "workbook")


@router.message(F.text == "📷 Сдать практику")
async def submit_practice_start(message: Message, state: FSMContext):
    await state.clear()
    if not await _intro_ok(message.from_user.id, message):
        return
    await _begin_submit(message.from_user.id, message, state, "practice")


@router.callback_query(F.data == "student_submit")
async def submit_start_cb(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    if not await _intro_ok(call.from_user.id, call.message):
        return
    await _begin_submit(call.from_user.id, call.message, state, "workbook")


def _type_label(t: str) -> str:
    return "практику" if t == "practice" else "конспект (РТ)"


def _type_emoji(t: str) -> str:
    return "📷" if t == "practice" else "📄"


async def _begin_submit(uid: int, target, state: FSMContext, sub_type: str = "workbook"):
    student = await crud.get_student_by_tg(uid)
    if not student:
        await target.answer("❌ Не нашёл тебя в системе. Обратись к куратору.")
        return
    sections = await crud.get_sections(student.group_id, active_only=True)
    if not sections:
        await target.answer(
            "📭 Куратор ещё не открыл раздел для сдачи.\n"
            "Как только он создаст раздел (например «Анатомия»), ты сможешь сдавать.")
        return
    realname = f"{student.first_name} {student.last_name}".strip()
    await state.update_data(realname=realname, photos=[], submit_type=sub_type)

    head = "📷 <b>Сдача практики</b>" if sub_type == "practice" else "📤 <b>Сдача РТ</b>"
    if len(sections) == 1:
        await state.update_data(section_id=sections[0].id)
        await _ask_name(target, state, sections[0], sub_type)
    else:
        rows = [[InlineKeyboardButton(text=f"📚 {s.name}", callback_data=f"sub_sec:{s.id}")]
                for s in sections]
        await state.set_state(StudentStates.submit_pick_section)
        await target.answer(f"{head}\n\nВыбери предмет (раздел) 👇",
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(StudentStates.submit_pick_section, F.data.startswith("sub_sec:"))
async def submit_pick_section(call: CallbackQuery, state: FSMContext):
    await call.answer()
    sid = int(call.data.split(":")[1])
    sec = await crud.get_section(sid)
    if not sec:
        await call.message.answer("❌ Раздел не найден.")
        return
    data = await state.get_data()
    await state.update_data(section_id=sid)
    await _ask_name(call.message, state, sec, data.get("submit_type", "workbook"))


async def _ask_name(target, state: FSMContext, section, sub_type: str):
    await state.set_state(StudentStates.submit_name)
    await target.answer(
        f"📚 Раздел: <b>{html.escape(section.name)}</b>\n\n"
        f"<b>Шаг 1 — Введи название файла</b> (это {_type_label(sub_type)})\n"
        "Например: <b>РТ №3</b> или <b>Практика урок 5</b>.\n"
        "Под этим именем PDF сохранится и придёт куратору 👇",
        parse_mode="HTML",
    )


@router.message(StudentStates.submit_name, F.text)
async def submit_name(message: Message, state: FSMContext):
    fname = message.text.strip()
    await state.update_data(fname=fname)
    data = await state.get_data()
    sec = await crud.get_section(data.get("section_id"))
    if not sec:
        await message.answer("❌ Раздел не найден, начни заново.")
        await state.clear()
        return
    rows = []
    row = []
    for w in range(1, sec.weeks + 1):
        row.append(InlineKeyboardButton(text=f"Неделя {w}", callback_data=f"sub_wk:{w}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    await state.set_state(StudentStates.submit_pick_week)
    await message.answer(f"✅ Название принято: <b>{html.escape(fname)}</b>", parse_mode="HTML")
    await message.answer(
        f"<b>Шаг 2 — За какую неделю раздела «{html.escape(sec.name)}»?</b>\n"
        "Выбери неделю 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(StudentStates.submit_pick_week, F.data.startswith("sub_wk:"))
async def submit_pick_week(call: CallbackQuery, state: FSMContext):
    await call.answer()
    week = int(call.data.split(":")[1])
    data = await state.get_data()
    sub_type = data.get("submit_type", "workbook")
    section_id = data.get("section_id")
    student = await crud.get_student_by_tg(call.from_user.id)
    sec = await crud.get_section(section_id)

    # защита от повторной сдачи
    if student and await crud.has_submission(student.id, section_id, week, sub_type):
        await state.clear()
        what = "практику" if sub_type == "practice" else "конспект"
        await call.message.answer(
            f"✅ Ты уже сдал(а) {what} по разделу «{sec.name if sec else '—'}», неделя {week}.\n\n"
            "Повторно сдать нельзя. Если нужно пересдать — попроси куратора удалить "
            "прежнюю работу, потом сможешь отправить заново.")
        return

    await state.update_data(week=week, photos=[])
    await state.set_state(StudentStates.submit_photos)
    await call.message.answer(
        f"✅ Неделя {week}\n\n"
        "────────────────\n"
        "Шаг 3 — Отправь фото\n\n"
        "📸 Фотографируй страницы и отправляй по одному. Можно сразу по 10–20 фото.\n\n"
        "• Хорошее освещение\n• Телефон ровно над листом\n• Текст читаем\n\n"
        "Когда все страницы отправлены — нажми «📄 Отправить в PDF»."
    )


@router.message(StudentStates.submit_photos, F.photo)
async def submit_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    photos: list[str] = data.get("photos", [])
    # берём самое крупное фото
    file_id = message.photo[-1].file_id
    photos.append(file_id)
    await state.update_data(photos=photos)
    await message.answer(
        f"✅ Фото #{len(photos)} добавлено\n"
        f"Всего принято: {len(photos)} страниц\n\n"
        "Если ещё не все страницы — продолжай отправлять.\n"
        "Когда всё готово — нажми кнопку ниже 👇",
        reply_markup=send_pdf_kb(),
    )


@router.message(StudentStates.submit_photos, F.document)
async def submit_doc_as_photo(message: Message, state: FSMContext):
    # на случай если фото прислано документом-изображением
    if message.document.mime_type and message.document.mime_type.startswith("image/"):
        data = await state.get_data()
        photos: list[str] = data.get("photos", [])
        photos.append(message.document.file_id)
        await state.update_data(photos=photos)
        await message.answer(
            f"✅ Фото #{len(photos)} добавлено\nВсего принято: {len(photos)} страниц",
            reply_markup=send_pdf_kb(),
        )
    else:
        await message.answer("📸 Пришли именно фото страниц. Когда закончишь — нажми «📄 Отправить в PDF».",
                             reply_markup=send_pdf_kb())


@router.callback_query(StudentStates.submit_photos, F.data == "student_make_pdf")
async def make_pdf(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    data = await state.get_data()
    photos: list[str] = data.get("photos", [])
    file_label: str = (data.get("fname") or "Работа").strip()
    if not photos:
        await call.message.answer("📸 Сначала пришли хотя бы одно фото.")
        return

    await call.message.answer(
        f"⏳ Шаг 3 из 3 — Создаю PDF из {len(photos)} фото...\n"
        "Это займёт несколько секунд. Пожалуйста, подождите."
    )

    # скачиваем фото и собираем PDF
    raw_images: list[bytes] = []
    for fid in photos:
        try:
            f = await bot.get_file(fid)
            buf = await bot.download_file(f.file_path)
            raw_images.append(buf.read())
        except Exception:
            continue

    try:
        pdf_bytes = pdf_builder.build_pdf(raw_images)
    except Exception:
        await call.message.answer("❌ Что-то пошло не так при сборке PDF. Попробуй ещё раз.")
        return

    student = await crud.get_student_by_tg(call.from_user.id)
    group = await crud.get_group(student.group_id)
    realname = f"{student.first_name} {student.last_name}".strip()
    section_id = data.get("section_id")
    week = int(data.get("week") or 0)
    sub_type = data.get("submit_type", "workbook")
    section = await crud.get_section(section_id) if section_id else None
    sec_name = section.name if section else "—"

    # повторная проверка дубля (на случай гонки)
    if section and await crud.has_submission(student.id, section_id, week, sub_type):
        await state.clear()
        what = "практику" if sub_type == "practice" else "конспект"
        await call.message.answer(
            f"✅ Ты уже сдал(а) {what} по «{sec_name}», неделя {week}. "
            "Попроси куратора удалить прежнюю работу для пересдачи.")
        return

    # дедлайн-контроль
    is_late, late_min = await _check_late(student.curator_id)

    # имя PDF — раздел + неделя + название (убираем недопустимые символы)
    kind = "практика" if sub_type == "practice" else "РТ"
    raw_name = f"{sec_name}_{kind}_неделя{week}_{file_label}" if section else file_label
    safe = re.sub(r'[\\/:*?"<>|\n\r\t]', "", raw_name).strip().replace(" ", "_")
    pdf_name = (safe or "Работа") + ".pdf"

    sent = await bot.send_document(
        call.from_user.id, BufferedInputFile(pdf_bytes, pdf_name),
        caption="📄 Твоя работа собрана в PDF и отправлена куратору."
    )
    pdf_file_id = sent.document.file_id

    sub = await crud.add_submission(
        student_id=student.id, pdf_file_id=pdf_file_id, submitted_name=file_label,
        curator_id=student.curator_id, section_id=section_id, week=week, sub_type=sub_type,
        is_late=is_late, late_by_minutes=late_min,
    )

    kind_word = "Практика" if sub_type == "practice" else "Рабочая тетрадь"
    if is_late:
        await call.message.answer(
            "⚠️ Работа принята.\n"
            f"Дедлайн был пройден, ты сдал(а) на +{late_min} мин позже.\n"
            "Куратор уведомлён."
        )
    else:
        await call.message.answer(
            f"🎉 <b>{kind_word} сдана!</b>\n\n"
            f"👤 {html.escape(realname)}\n"
            f"📚 Раздел: <b>{html.escape(sec_name)}</b> · Неделя {week}\n"
            f"📄 Файл: <b>{html.escape(file_label)}</b>\n"
            f"Страниц: {len(photos)}\n"
            "📨 PDF отправлен куратору\n\n"
            "────────────────\n"
            "Куратор получил твою работу и проверит её.",
            parse_mode="HTML",
            reply_markup=student_menu(),
        )

    # уведомление куратору + Google Sheets
    await notifications.notify_submission(bot, sub.id)
    status = f"просрочено +{late_min} мин" if is_late else "вовремя"
    await sheets.append_submission(realname, group.name if group else "—",
                                   roles.fmt_absolute(sub.submitted_at_utc), status)

    await state.clear()


async def _check_late(curator_id: int) -> tuple[bool, int]:
    """Просрочена ли сдача относительно дедлайна куратора.

    Берём ближайший ПРОШЕДШИЙ дедлайн. Если с него прошло не больше
    12 часов — сдача считается просроченной на это число минут.
    Иначе ученик сдаёт заранее к следующему дедлайну → вовремя.
    """
    dl = await crud.get_deadline(curator_id)
    if not dl:
        return False, 0
    now = datetime.now(timezone.utc)
    h, m = map(int, dl.deadline_time_utc.split(":"))
    anchor = now.replace(hour=h, minute=m, second=0, microsecond=0)
    delta_days = (now.weekday() - dl.weekday) % 7
    deadline = anchor - timedelta(days=delta_days)
    if deadline > now:
        deadline -= timedelta(days=7)
    diff_min = int((now - deadline).total_seconds() // 60)
    if 0 < diff_min <= 12 * 60:
        return True, diff_min
    return False, 0


# ─── МОИ РАБОТЫ ─────────────────────────────────────────────────

@router.message(F.text == "📁 Мои работы")
async def my_works(message: Message, state: FSMContext):
    if not await _intro_ok(message.from_user.id, message):
        return
    student = await crud.get_student_by_tg(message.from_user.id)
    subs = await crud.get_submissions(student_id=student.id)
    if not subs:
        await message.answer("📁 У тебя пока нет сданных работ.")
        return
    lines = ["📁 Твои работы:\n"]
    kb_rows = []
    for i, sub in enumerate(reversed(subs), start=1):
        mark = " ⚠️" if sub.is_late else ""
        lines.append(f"   📤 РТ #{i} — {roles.fmt_absolute(sub.submitted_at_utc)}{mark}")
        kb_rows.append([InlineKeyboardButton(
            text=f"📂 Открыть РТ #{i}", callback_data=f"open_my:{sub.id}")])
    await message.answer("\n".join(lines),
                         reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(F.data.startswith("open_my:"))
async def open_my(call: CallbackQuery):
    await call.answer()
    sub_id = int(call.data.split(":")[1])
    sub = await crud.get_submission(sub_id)
    student = await crud.get_student_by_tg(call.from_user.id)
    if not sub or not student or sub.student_id != student.id:
        await call.message.answer("❌ Файл не найден.")
        return
    await call.message.answer_document(sub.pdf_file_id)
