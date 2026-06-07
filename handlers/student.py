"""Хэндлеры ученика: рабочие тетради, сдача РТ, мои работы."""
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


# ─── РАБОЧИЕ ТЕТРАДИ ────────────────────────────────────────────

@router.message(F.text == "📚 Рабочие тетради")
async def list_workbooks(message: Message, state: FSMContext):
    wbs = await crud.get_workbooks()
    if not wbs:
        await message.answer("📚 Пока нет загруженных рабочих тетрадей.")
        return
    lines = ["📚 Доступные рабочие тетради:\n"]
    for w in wbs:
        lines.append(f"   №{w.serial:03d} — {w.topic}")
    lines.append("\nНапиши серийный номер — я сразу пришлю PDF 👇")
    await message.answer("\n".join(lines))
    await state.set_state(StudentStates.get_workbook)


@router.message(StudentStates.get_workbook, F.text.regexp(r"^\s*№?\s*\d+\s*$"))
async def send_workbook(message: Message, state: FSMContext):
    serial = int("".join(ch for ch in message.text if ch.isdigit()))
    wb = await crud.get_workbook_by_serial(serial)
    if not wb:
        await message.answer("❌ Такого номера нет. Выбери номер из списка выше.")
        return
    await message.answer_document(wb.file_id, caption=f"📄 №{wb.serial:03d} — {wb.topic}")
    await state.clear()


# ─── СДАТЬ РТ ───────────────────────────────────────────────────

@router.message(F.text == "📤 Сдать РТ")
async def submit_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(StudentStates.submit_name)
    await message.answer(
        "📤 Сдача рабочей тетради\n\n"
        "Сейчас вы отправите фото выполненной работы, бот соберёт их "
        "в один PDF и отправит вашему куратору.\n\n"
        "────────────────\n"
        "Шаг 1 из 3 — Введите ФИО\n\n"
        "Напишите своё полное ФИО — оно будет указано в PDF.\n\n"
        "📝 Пример: Иванов Иван Иванович\n\n"
        "❗ Введите имя точно как в журнале — куратор будет искать работу по нему."
    )


@router.callback_query(F.data == "student_submit")
async def submit_start_cb(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await submit_start(call.message, state)


@router.message(StudentStates.submit_name, F.text)
async def submit_name(message: Message, state: FSMContext):
    name = message.text.strip()
    await state.update_data(name=name, photos=[])
    await state.set_state(StudentStates.submit_photos)
    await message.answer(f"✅ ФИО принято: {name}")
    await message.answer(
        "────────────────\n"
        "Шаг 2 из 3 — Отправьте фото\n\n"
        "📸 Фотографируйте страницы и отправляйте по одному.\n"
        "Можно отправлять сразу по 10–20 фото — бот сохранит все по порядку.\n\n"
        "Советы для хорошего качества:\n"
        "• Хорошее освещение — не в темноте\n"
        "• Держите телефон ровно над листом\n"
        "• Весь текст должен быть виден и читаем\n\n"
        "После каждого фото бот покажет счётчик.\n"
        "Когда все страницы отправлены — нажмите «📄 Отправить в PDF»."
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
    name: str = data.get("name", "")
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

    # дедлайн-контроль
    is_late, late_min = await _check_late(student.curator_id)

    fname = f"РТ_{name.replace(' ', '_')}.pdf"
    # отправляем сам PDF ученику как файл, чтобы получить надёжный file_id
    sent = await bot.send_document(
        call.from_user.id, BufferedInputFile(pdf_bytes, fname),
        caption="📄 Ваша работа собрана в PDF и отправлена куратору."
    )
    pdf_file_id = sent.document.file_id

    sub = await crud.add_submission(
        student_id=student.id, pdf_file_id=pdf_file_id, submitted_name=name,
        curator_id=student.curator_id, is_late=is_late, late_by_minutes=late_min,
    )

    if is_late:
        await call.message.answer(
            "⚠️ Работа принята.\n"
            f"Дедлайн был пройден, ты сдал(а) на +{late_min} мин позже.\n"
            "Куратор уведомлён."
        )
    else:
        await call.message.answer(
            "🎉 Рабочая тетрадь успешно сдана!\n\n"
            f"👤 ФИО: {name}\n"
            f"📄 Страниц: {len(photos)}\n"
            "📨 PDF отправлен куратору\n\n"
            "────────────────\n"
            "Куратор получил вашу работу и проверит её.",
            reply_markup=student_menu(),
        )

    # уведомление куратору + Google Sheets
    await notifications.notify_submission(bot, sub.id)
    status = f"просрочено +{late_min} мин" if is_late else "вовремя"
    await sheets.append_submission(name, group.name if group else "—",
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
