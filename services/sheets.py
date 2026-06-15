"""Google Sheets — живой журнал сдач.

Подключается, если задан GOOGLE_CREDS_FILE и админ ввёл ссылку на таблицу
(доступ «Редактор» выдан service-аккаунту). Иначе тихо отключено.

Ведём два листа:
  • «Лента сдач» — плоская лента: каждая новая сдача добавляется строкой.
  • «Журнал»     — структурный отчёт с отступами:
        ГРУППА (куратор, год/время создания, учеников)
            Раздел (даты, период, недель)
                Неделя (диапазон, дедлайн)
                    ученик · статус · время
"""
from config import GOOGLE_CREDS_FILE
from database import crud
from services import roles

try:
    import gspread
    from google.oauth2.service_account import Credentials
    _AVAILABLE = True
except Exception:
    _AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


async def _client():
    if not _AVAILABLE or not GOOGLE_CREDS_FILE:
        return None
    url = await crud.get_setting("gsheet_url")
    if not url:
        return None
    try:
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=SCOPES)
        gc = gspread.authorize(creds)
        return gc.open_by_url(url)
    except Exception:
        return None


def is_configured_sync() -> bool:
    return bool(_AVAILABLE and GOOGLE_CREDS_FILE)


async def append_feed(sub_id: int) -> None:
    """Добавляет одну строку в «Лента сдач» (быстро, на каждую сдачу)."""
    sh = await _client()
    if sh is None:
        return
    try:
        sub = await crud.get_submission(sub_id)
        if not sub:
            return
        student = await crud.get_student(sub.student_id)
        group = await crud.get_group(student.group_id) if student else None
        sec = await crud.get_section(sub.section_id) if sub.section_id else None
        curator = await crud.get_user_by_tg(sub.curator_id)
        cur_name = ("@" + curator.username) if curator and curator.username else (
            (curator.first_name if curator else "") or str(sub.curator_id))
        realname = f"{student.first_name} {student.last_name}".strip() if student else sub.submitted_name
        kind = "Практика" if sub.type == "practice" else "РТ"
        unit = f"Практика {sub.week}" if sub.type == "practice" else f"Неделя {sub.week}"
        status = f"ПРОСРОЧЕНО +{roles.fmt_late(sub.late_by_minutes)}" if sub.is_late else "вовремя"
        when = roles.fmt_absolute(sub.submitted_at_utc)
        try:
            ws = sh.worksheet("Лента сдач")
        except Exception:
            ws = sh.add_worksheet("Лента сдач", rows=1000, cols=8)
            ws.append_row(["Дата/время", "Группа", "Куратор", "Раздел",
                           "Тип", "Период", "Ученик", "Статус"])
        ws.append_row([when, group.name if group else "—", cur_name,
                       sec.name if sec else "—", kind, unit, realname, status])
    except Exception:
        pass


async def rebuild_journal() -> bool:
    """Полностью пересобирает структурный лист «Журнал». Возвращает True при успехе."""
    sh = await _client()
    if sh is None:
        return False
    try:
        groups = await crud.get_groups(include_hidden=True)
        rows = [["📒 ЖУРНАЛ СДАЧ — обновлено", roles.fmt_absolute(roles.now_local()), "", ""],
                ["", "", "", ""]]
        for g in groups:
            curator = await crud.get_user_by_tg(g.curator_id)
            cur_name = ("@" + curator.username) if curator and curator.username else (
                (curator.first_name if curator else "") or str(g.curator_id))
            created = roles.to_local(g.created_at) if g.created_at else None
            created_str = f"Создана: {created.year}, {created:%d.%m %H:%M}" if created else "Создана: —"
            hidden = " (архив)" if getattr(g, "hidden", False) else ""
            n = await crud.count_students(g.id)
            rows.append([f"ГРУППА: {g.name}{hidden}", f"Куратор: {cur_name}",
                         created_str, f"Учеников: {n}"])

            students = await crud.get_students(curator_id=g.curator_id, group_id=g.id)
            sections = await crud.get_sections(g.id)
            if not sections:
                rows.append(["", "(разделов пока нет)", "", ""])
            for sec in sections:
                total = roles.section_total_weeks(sec)
                period = getattr(sec, "period_days", 7) or 7
                rows.append(["", f"Раздел: {sec.name}",
                             f"{roles.section_start_str(sec)}–{roles.section_end_str(sec)}",
                             f"{period} дн/нед · {total} нед"])
                for w in range(1, total + 1):
                    subs = await crud.get_submissions(section_id=sec.id, week=w, sub_type="workbook")
                    last = {}
                    for s in subs:
                        last.setdefault(s.student_id, s)
                    done = sum(1 for st in students if st.id in last)
                    rows.append(["", "", f"Неделя {w} ({roles.section_week_range_str(sec, w)})",
                                 f"дедлайн {roles.section_deadline_str(sec, w)} · сдали {done}/{len(students)}"])
                    for st in students:
                        sub = last.get(st.id)
                        if sub and sub.is_late:
                            detail = f"⚠️ {st.first_name} {st.last_name} · просрочено +{roles.fmt_late(sub.late_by_minutes)} · {roles.to_local(sub.submitted_at_utc):%d.%m %H:%M}"
                        elif sub:
                            detail = f"✅ {st.first_name} {st.last_name} · вовремя · {roles.to_local(sub.submitted_at_utc):%d.%m %H:%M}"
                        else:
                            detail = f"❌ {st.first_name} {st.last_name} · не сдал"
                        rows.append(["", "", "", detail])
            rows.append(["", "", "", ""])  # пустая строка между группами

        try:
            ws = sh.worksheet("Журнал")
            ws.clear()
        except Exception:
            ws = sh.add_worksheet("Журнал", rows=max(200, len(rows) + 20), cols=6)
        ws.update("A1", rows, value_input_option="RAW")
        # жирным шапку и строки групп
        try:
            ws.format("A1:D1", {"textFormat": {"bold": True}})
        except Exception:
            pass
        return True
    except Exception:
        return False
