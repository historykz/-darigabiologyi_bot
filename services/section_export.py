"""Экспорт сдач в Excel.

• section_xlsx(section_id) — один раздел: листы по неделям РТ + листы практики.
• group_xlsx(group_id)     — вся группа: лист на раздел (РТ по неделям, затем практика).

Везде: ученики по алфавиту (Фамилия Имя), показываем и тех, кто не сдал (❌),
дата/время сдачи, пометка просрочки. Только зарегистрированные ученики.
"""
import io

from openpyxl import Workbook as XlWorkbook
from openpyxl.styles import Font, PatternFill

from database import crud
from services import roles

_HEAD = Font(bold=True)
_GROUP = Font(bold=True, size=12)
_LATE = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
_MISS = PatternFill(start_color="FCE4E4", end_color="FCE4E4", fill_type="solid")


def _fio(st) -> str:
    return f"{st.last_name} {st.first_name}".strip()


def _status(sub):
    if not sub:
        return "❌ не сдал(а)", "—", _MISS
    if sub.is_late:
        return f"⚠️ просрочено +{roles.fmt_late(sub.late_by_minutes)}", \
               roles.fmt_absolute(sub.submitted_at_utc), _LATE
    return "✅ вовремя", roles.fmt_absolute(sub.submitted_at_utc), None


async def section_xlsx(section_id: int) -> bytes:
    sec = await crud.get_section(section_id)
    students = await crud.get_students(curator_id=sec.curator_id, group_id=sec.group_id)
    total = roles.section_total_weeks(sec)
    practices = max(1, getattr(sec, "practices", 2) or 2)

    wb = XlWorkbook()
    wb.remove(wb.active)

    # РТ — лист на неделю
    for week in range(1, total + 1):
        subs = await crud.get_submissions(curator_id=sec.curator_id,
                                          section_id=section_id, week=week, sub_type="workbook")
        last = {}
        for sub in subs:
            last.setdefault(sub.student_id, sub)
        ws = wb.create_sheet(f"РТ нед {week}"[:31])
        ws.append([f"{sec.name} · РТ · Неделя {week} ({roles.section_week_range_str(sec, week)}) · "
                   f"дедлайн {roles.section_deadline_str(sec, week)}"])
        ws["A1"].font = _GROUP
        ws.append(["Ученик (Фамилия Имя)", "Статус", "Дата сдачи", "Файл"])
        for c in ws[2]:
            c.font = _HEAD
        for st in students:
            sub = last.get(st.id)
            stext, when, fill = _status(sub)
            ws.append([_fio(st), stext, when, sub.submitted_name if sub else "—"])
            if fill:
                for c in ws[ws.max_row]:
                    c.fill = fill
        for col, w in zip("ABCD", (28, 26, 22, 24)):
            ws.column_dimensions[col].width = w

    # Практика — лист на каждую
    for p in range(1, practices + 1):
        subs = await crud.get_submissions(curator_id=sec.curator_id,
                                          section_id=section_id, week=p, sub_type="practice")
        last = {}
        for sub in subs:
            last.setdefault(sub.student_id, sub)
        ws = wb.create_sheet(f"Практика {p}"[:31])
        ws.append([f"{sec.name} · Практика {p}"])
        ws["A1"].font = _GROUP
        ws.append(["Ученик (Фамилия Имя)", "Статус", "Дата сдачи"])
        for c in ws[2]:
            c.font = _HEAD
        for st in students:
            sub = last.get(st.id)
            # практика без дедлайна — либо сдал, либо нет
            if sub:
                stext, when, fill = "✅ сдал(а)", roles.fmt_absolute(sub.submitted_at_utc), None
            else:
                stext, when, fill = "❌ не сдал(а)", "—", _MISS
            ws.append([_fio(st), stext, when])
            if fill:
                for c in ws[ws.max_row]:
                    c.fill = fill
        for col, w in zip("ABC", (28, 22, 22)):
            ws.column_dimensions[col].width = w

    if not wb.sheetnames:
        wb.create_sheet("Пусто")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def group_xlsx(group_id: int) -> bytes:
    """Вся группа: лист на раздел (РТ по неделям, затем практика)."""
    sections = await crud.get_sections(group_id)
    students = await crud.get_students(group_id=group_id)

    wb = XlWorkbook()
    wb.remove(wb.active)

    for sec in sections:
        total = roles.section_total_weeks(sec)
        practices = max(1, getattr(sec, "practices", 2) or 2)
        ws = wb.create_sheet((sec.name[:28] or "Раздел"))
        ws.append([f"Раздел «{sec.name}» · {roles.section_start_str(sec)}–{roles.section_end_str(sec)}"])
        ws["A1"].font = _GROUP
        ws.append(["Период", "Ученик (Фамилия Имя)", "Статус", "Дата сдачи"])
        for c in ws[2]:
            c.font = _HEAD

        for week in range(1, total + 1):
            subs = await crud.get_submissions(curator_id=sec.curator_id,
                                              section_id=sec.id, week=week, sub_type="workbook")
            last = {}
            for sub in subs:
                last.setdefault(sub.student_id, sub)
            label = f"РТ Неделя {week} (дедлайн {roles.section_deadline_str(sec, week)})"
            for st in students:
                sub = last.get(st.id)
                stext, when, fill = _status(sub)
                ws.append([label, _fio(st), stext, when])
                if fill:
                    for c in ws[ws.max_row]:
                        c.fill = fill

        for p in range(1, practices + 1):
            subs = await crud.get_submissions(curator_id=sec.curator_id,
                                              section_id=sec.id, week=p, sub_type="practice")
            last = {}
            for sub in subs:
                last.setdefault(sub.student_id, sub)
            for st in students:
                sub = last.get(st.id)
                if sub:
                    stext, when, fill = "✅ сдал(а)", roles.fmt_absolute(sub.submitted_at_utc), None
                else:
                    stext, when, fill = "❌ не сдал(а)", "—", _MISS
                ws.append([f"Практика {p}", _fio(st), stext, when])
                if fill:
                    for c in ws[ws.max_row]:
                        c.fill = fill

        for col, w in zip("ABCD", (30, 28, 26, 22)):
            ws.column_dimensions[col].width = w

    if not wb.sheetnames:
        wb.create_sheet("Пусто")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
