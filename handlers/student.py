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
        await _after_section(target, state, sections[0], sub_type)
    else:
        rows = [[InlineKeyboardButton(text=f"📚 {s.name}", callback_data=f"sub_sec:{s.id}")]
                for s in sections]
        await state.set_state(StudentStates.submit_pick_section)
        await target.answer(
            f"{head}\n\n"
            "Раздел — это предмет, который вы сейчас проходите.\n"
            "Нажми на нужный 👇",
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
    await _after_section(call.message, state, sec, data.get("submit_type", "workbook"))


async def _after_section(target, state: FSMContext, section, sub_type: str):
    """Практика — сразу выбор недели (1 или 2, без названия). Конспект — спросить название."""
    if sub_type == "practice":
        await _ask_week(target, state, section, sub_type)
    else:
        await _ask_name(target, state, section, sub_type)


async def _ask_week(target, state: FSMContext, section, sub_type: str):
    if sub_type == "practice":
        total = max(1, getattr(section, "practices", 2) or 2)
        row, rows = [], []
        for p in range(1, total + 1):
            row.append(InlineKeyboardButton(text=f"Практика {p}", callback_data=f"sub_wk:{p}"))
            if len(row) == 2:
                rows.append(row); row = []
        if row:
            rows.append(row)
        await state.set_state(StudentStates.submit_pick_week)
        await target.answer(
            f"📷 Практика · раздел <b>{html.escape(section.name)}</b>\n\n"
            f"<b>Какая это практика по счёту?</b> (всего их {total})\n"
            "Если сдаёшь первый раз — жми «Практика 1», второй раз — «Практика 2» 👇",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        return
    total = roles.section_total_weeks(section)
    cur = roles.section_current_week(section)
    rows, row = [], []
    for w in range(1, total + 1):
        rng = roles.section_week_range_str(section, w)
        is_cur = " ◀" if w == cur else ""
        row.append(InlineKeyboardButton(text=f"Нед.{w}: {rng}{is_cur}", callback_data=f"sub_wk:{w}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    await state.set_state(StudentStates.submit_pick_week)
    cur_txt = f"(сейчас идёт неделя {cur})" if 1 <= cur <= total else ""
    await target.answer(
        f"<b>Шаг 2 — За какую неделю «{html.escape(section.name)}»?</b> {cur_txt}\n"
        "На кнопке — диапазон дат. За текущую — вовремя. За прошлую — просрочка. За будущую — нельзя 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _ask_name(target, state: FSMContext, section, sub_type: str):
    await state.set_state(StudentStates.submit_name)
    await target.answer(
        f"📚 Раздел: <b>{html.escape(section.name)}</b>\n\n"
        "<b>Шаг 1 — Введи название файла</b>\n"
        "Это просто подпись твоей работы, чтобы куратор понял, что ты сдаёшь.\n"
        "Например: <b>РТ №3</b> или <b>Конспект урок 5</b>.\n"
        "Напиши название одним сообщением 👇",
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
    total2 = roles.section_total_weeks(sec)
    cur2 = roles.section_current_week(sec)
    rows = []; row = []
    for w in range(1, total2 + 1):
        rng = roles.section_week_range_str(sec, w)
        is_cur = " ◀" if w == cur2 else ""
        row.append(InlineKeyboardButton(text=f"Нед.{w}: {rng}{is_cur}", callback_data=f"sub_wk:{w}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    await state.set_state(StudentStates.submit_pick_week)
    await message.answer(f"✅ Название принято: <b>{html.escape(fname)}</b>", parse_mode="HTML")
    await message.answer(
        f"<b>Шаг 2 — За какую неделю «{html.escape(sec.name)}»?</b>\n"
        "На кнопке — диапазон дат. ◀ — текущая неделя 👇",
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
        if sub_type == "practice":
            await call.message.answer(
                f"\u2705 Ты уже сдал(а) практику {week} по разделу \u00ab{sec.name if sec else '\u2014'}\u00bb.\n\n"
                "Сдать её повторно нельзя \u2014 так бот защищает от случайных двойных отправок.\n"
                "Если нужно заменить работу \u2014 напиши куратору, он удалит старую.")
        else:
            await call.message.answer(
                f"\u2705 Ты уже сдал(а) конспект по разделу \u00ab{sec.name if sec else '\u2014'}\u00bb, неделя {week}.\n\n"
                "Сдать повторно нельзя \u2014 так бот защищает от случайных двойных отправок.\n"
                "Если нужно заменить работу \u2014 напиши куратору, он удалит старую.")
        return

    # практика \u2014 без недельного графика, сразу к фото
    if sub_type == "practice":
        await _go_photos(call.message, state, sec, week, sub_type)
        return

    cur = roles.section_current_week(sec) if sec else 1
    if cur == 0:
        await state.clear()
        await call.message.answer(
            f"\u23f3 Раздел \u00ab{sec.name}\u00bb ещё не начался (старт {roles.section_start_str(sec)}).\n"
            "Сдавать пока рано \u2014 дождись начала.")
        return
    if week > cur:
        await state.clear()
        # когда откроется именно следующая неделя (cur+1)
        if week == cur + 1:
            opens = roles.section_week_start_local(sec, cur + 1)
            await call.message.answer(
                f"🚫 Сейчас идёт неделя {cur} раздела «{sec.name}».\n"
                f"Неделю {week} пока сдать нельзя — она откроется только после того, "
                f"как закончится неделя {cur} (после {roles.section_deadline_str(sec, cur)}).\n"
                f"Тогда, с {opens.day:02d}.{opens.month:02d}, можно будет сдать неделю {week}.\n\n"
                f"Сейчас сдавай неделю {cur} (текущую) или прошлые недели.")
        else:
            await call.message.answer(
                f"🚫 Сейчас идёт неделя {cur} раздела «{sec.name}», а неделя {week} ещё не началась.\n"
                "Недели открываются по очереди: пока не закончится текущая, следующую сдать нельзя.\n"
                f"Сейчас можно сдать неделю {cur} или прошлые.")
        return
    if week == cur:
        await _go_photos(call.message, state, sec, week, sub_type)
        return
    # прошлая неделя \u2014 дедлайн прошёл, подтверждение
    await state.update_data(pending_week=week)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="\u2705 Да, уверен(а)", callback_data=f"sub_wk_yes:{week}"),
        InlineKeyboardButton(text="\u274c Отмена", callback_data="sub_wk_no"),
    ]])
    await call.message.answer(
        f"\u26a0\ufe0f Сейчас идёт неделя {cur} раздела \u00ab{sec.name}\u00bb.\n"
        f"Ты выбрал(а) неделю {week} \u2014 её дедлайн уже прошёл "
        f"(был до {roles.section_deadline_str(sec, week)}).\n\n"
        "Точно хочешь сдать за прошлую неделю? Работа будет помечена как просроченная.",
        reply_markup=kb)


@router.callback_query(StudentStates.submit_pick_week, F.data == "sub_wk_no")
async def submit_week_cancel(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await call.message.answer("Отменено. Можешь начать заново, когда будешь готов(а).")


@router.callback_query(StudentStates.submit_pick_week, F.data.startswith("sub_wk_yes:"))
async def submit_week_confirm(call: CallbackQuery, state: FSMContext):
    await call.answer()
    week = int(call.data.split(":")[1])
    data = await state.get_data()
    sec = await crud.get_section(data.get("section_id"))
    await _go_photos(call.message, state, sec, week, data.get("submit_type", "workbook"))


async def _go_photos(target, state: FSMContext, sec, week: int, sub_type: str):
    await state.update_data(week=week, photos=[])
    await state.set_state(StudentStates.submit_photos)
    if sub_type == "practice":
        await target.answer(
            f"\u2705 Практика {week} \u00b7 раздел \u00ab{sec.name if sec else '\u2014'}\u00bb\n\n"
            "\U0001f4f7 Отправь снимок экрана выполненной практики.\n"
            "Снимок экрана \u2014 это обычное фото/скриншот с телефона или компьютера.\n"
            "Можно несколько кадров \u2014 я соберу их в один PDF.\n\n"
            "Когда всё отправил(а) \u2014 нажми кнопку \u00ab\U0001f4c4 Отправить в PDF\u00bb, и работа сама уйдёт куратору.")
        return
    await target.answer(
        f"\u2705 Неделя {week} \u00b7 раздел \u00ab{sec.name if sec else '\u2014'}\u00bb\n\n"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        "Шаг 3 \u2014 Отправь фото\n\n"
        "\U0001f4f8 Фотографируй страницы и отправляй по одному. Можно сразу по 10\u201320 фото.\n\n"
        "\u2022 Хорошее освещение\n\u2022 Телефон ровно над листом\n\u2022 Текст читаем\n\n"
        "Когда все страницы отправлены \u2014 нажми кнопку \u00ab\U0001f4c4 Отправить в PDF\u00bb, "
        "и я соберу их в один файл и сам отправлю куратору.")


@router.message(StudentStates.submit_photos, F.photo)
async def collect_photo(message: Message, state: FSMContext):
    await _add_photo(message, state, message.photo[-1].file_id, message.bot)


@router.message(StudentStates.submit_photos, F.document)
async def collect_photo_doc(message: Message, state: FSMContext):
    """Картинка, отправленная как файл (без сжатия) — тоже принимаем."""
    doc = message.document
    mime = (doc.mime_type or "").lower()
    if mime.startswith("image/"):
        await _add_photo(message, state, doc.file_id, message.bot)
    else:
        await message.answer(
            "❗ Это не картинка. Пришли снимок экрана или фото страницы "
            "(обычным изображением). PDF-файлы прикреплять не нужно — я сам соберу PDF из фото.",
            reply_markup=send_pdf_kb())


async def _add_photo(message: Message, state: FSMContext, file_id: str, bot: Bot):
    """Добавляет фото в список, сохраняя порядок отправки (по message_id),
    и обновляет ОДНО сообщение-счётчик вместо спама на каждое фото."""
    data = await state.get_data()
    # храним пары [message_id, file_id] — message_id растёт по порядку отправки
    photos = data.get("photos", [])
    photos.append([message.message_id, file_id])
    counter_id = data.get("counter_msg_id")
    await state.update_data(photos=photos)

    n = len(photos)
    text = (f"📸 Принимаю фото… Принято: {n}\n"
            "Можешь слать ещё страницы по порядку. Когда всё — нажми «📄 Отправить в PDF».")
    # пробуем обновить уже отправленный счётчик; если нельзя — шлём один новый
    if counter_id:
        try:
            await bot.edit_message_text(chat_id=message.chat.id, message_id=counter_id,
                                        text=text, reply_markup=send_pdf_kb())
            return
        except Exception:
            pass
    try:
        sent = await message.answer(text, reply_markup=send_pdf_kb())
        await state.update_data(counter_msg_id=sent.message_id)
    except Exception:
        pass


@router.message(StudentStates.submit_photos)
async def submit_photos_hint(message: Message, state: FSMContext):
    """Любое другое сообщение в режиме сбора фото — мягкая подсказка, не «не понял команду»."""
    data = await state.get_data()
    n = len(data.get("photos", []))
    await message.answer(
        f"📸 Жду фото/снимки экрана. Сейчас принято: {n}.\n"
        "Пришли изображение или нажми «📄 Отправить в PDF» 👇",
        reply_markup=send_pdf_kb())


@router.callback_query(StudentStates.submit_photos, F.data == "student_make_pdf")
async def make_pdf(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    data = await state.get_data()
    raw_photos = data.get("photos", [])
    # сортируем по message_id (порядок отправки учеником), затем берём file_id
    ordered = []
    for item in raw_photos:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            ordered.append(item)
        else:  # старый формат (просто file_id) — на всякий случай
            ordered.append([0, item])
    ordered.sort(key=lambda x: x[0])
    photos: list[str] = [fid for _mid, fid in ordered]
    _t = data.get("submit_type", "workbook")
    _w = int(data.get("week") or 0)
    if _t == "practice":
        file_label = f"Практика {_w}" if _w else "Практика"
    else:
        file_label = (data.get("fname") or "Работа").strip()
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
    if sub_type == "practice":
        is_late, late_min = False, 0  # дедлайн относится только к РТ
    else:
        is_late, late_min = roles.section_is_week_late(section, week) if section else (False, 0)

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
        is_late=is_late, late_by_minutes=late_min, pages=len(photos),
    )

    kind_word = "Практика" if sub_type == "practice" else "Рабочая тетрадь"
    wk_label = f"Практика {week}" if sub_type == "practice" else f"Неделя {week}"
    if is_late:
        await call.message.answer(
            "⚠️ Работа принята, но СДАНА ПОСЛЕ ДЕДЛАЙНА.\n"
            f"📚 {sec_name} · Неделя {week}\n"
            f"⏰ Опоздание: +{_fmt_late(late_min)}\n"
            "Куратор увидит, что работа просрочена."
        )
    else:
        await call.message.answer(
            f"🎉 <b>{kind_word} сдана!</b>\n\n"
            f"👤 {html.escape(realname)}\n"
            f"📚 Раздел: <b>{html.escape(sec_name)}</b> · {wk_label}\n"
            f"📄 Файл: <b>{html.escape(file_label)}</b>\n"
            f"Страниц: {len(photos)}\n"
            "📨 PDF отправлен куратору\n\n"
            "────────────────\n"
            "Куратор получил твою работу и проверит её.",
            parse_mode="HTML",
            reply_markup=student_menu(),
        )

    # уведомление куратору + Google Sheets (живой журнал)
    await notifications.notify_submission(bot, sub.id)
    await sheets.append_feed(sub.id)
    await sheets.rebuild_journal()

    await state.clear()


def _fmt_late(mins: int) -> str:
    """Человеческое опоздание: мин / ч / дни."""
    if mins < 60:
        return f"{mins} мин"
    h, mm = divmod(mins, 60)
    if h < 24:
        return f"{h} ч" + (f" {mm} мин" if mm else "")
    d, hh = divmod(h, 24)
    return f"{d} дн" + (f" {hh} ч" if hh else "")


def _week_due_utc(section, week: int, dl) -> datetime | None:
    """Срок сдачи конкретной недели раздела (UTC).

    Неделя 1 — первый дедлайн (день недели + время куратора) на/после старта раздела,
    каждая следующая неделя — +7 дней.
    """
    if not section or not dl:
        return None
    try:
        h, m = map(int, dl.deadline_time_utc.split(":"))
    except Exception:
        return None
    start = section.created_at
    if start is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    days_ahead = (dl.weekday - start.weekday()) % 7
    first = (start + timedelta(days=days_ahead)).replace(
        hour=h, minute=m, second=0, microsecond=0)
    if first < start:
        first += timedelta(days=7)
    return first + timedelta(days=7 * (max(1, week) - 1))


async def _check_late_for(section, week: int, curator_id: int) -> tuple[bool, int]:
    """Просрочена ли сдача недели раздела. Любая отправка после срока — просрочка."""
    dl = await crud.get_deadline(curator_id)
    due = _week_due_utc(section, week, dl)
    if due is None:
        return False, 0
    now = datetime.now(timezone.utc)
    diff_min = int((now - due).total_seconds() // 60)
    if diff_min > 0:
        return True, diff_min
    return False, 0


# ─── МОИ РАБОТЫ ─────────────────────────────────────────────────

@router.message(F.text == "📁 Мои работы")
async def my_works(message: Message, state: FSMContext):
    if not await _intro_ok(message.from_user.id, message):
        return
    student = await crud.get_student_by_tg(message.from_user.id)
    subs = await crud.get_submissions(student_id=student.id)
    # только рабочие тетради (РТ), практику тут не показываем
    subs = [s for s in subs if s.type != "practice"]
    if not subs:
        await message.answer(
            "📁 У тебя пока нет сданных рабочих тетрадей.\n"
            "Здесь будут только РТ (конспекты), сгруппированные по разделам.")
        return

    # группируем по разделу
    by_section: dict[int, list] = {}
    for s in subs:
        by_section.setdefault(s.section_id or 0, []).append(s)

    lines = ["📁 Твои рабочие тетради (по разделам):\n"]
    kb_rows = []
    for sec_id, items in by_section.items():
        sec = await crud.get_section(sec_id) if sec_id else None
        sec_name = sec.name if sec else "Без раздела"
        lines.append(f"📚 <b>{sec_name}</b>:")
        for sub in sorted(items, key=lambda x: x.week or 0):
            if sub.is_late:
                lines.append(f"   ⚠️ Неделя {sub.week} — ПРОСРОЧЕНО · {roles.to_local(sub.submitted_at_utc):%d.%m %H:%M}")
            else:
                lines.append(f"   ✅ Неделя {sub.week} — вовремя · {roles.to_local(sub.submitted_at_utc):%d.%m %H:%M}")
            kb_rows.append([InlineKeyboardButton(
                text=f"📂 {sec_name} · Неделя {sub.week}", callback_data=f"open_my:{sub.id}")])
        lines.append("")
    await message.answer("\n".join(lines), parse_mode="HTML",
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
