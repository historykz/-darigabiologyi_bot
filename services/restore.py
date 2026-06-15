"""Восстановление из JSON-бэкапа (upsert по ключам)."""
import json
import secrets
from datetime import datetime

from sqlalchemy import select

from database.models import (
    Deadline,
    Group,
    Section,
    Setting,
    Student,
    Submission,
    User,
    Workbook,
)
from database.session import async_session


def parse(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8"))


def summarize(data: dict) -> dict:
    return {
        "curators": sum(1 for u in data.get("users", []) if u.get("role") == "curator"),
        "students": len(data.get("students", [])),
        "groups": len(data.get("groups", [])),
        "sections": len(data.get("sections", [])),
        "submissions": len(data.get("submissions", [])),
        "practice": sum(1 for x in data.get("submissions", []) if x.get("type") == "practice"),
        "workbooks": len(data.get("workbooks", [])),
    }


def _dt(iso: str | None):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except Exception:
        return None


async def restore(data: dict) -> dict:
    """Заливает данные в БД через upsert. Возвращает количество восстановленных."""
    counts = {"curators": 0, "students": 0, "groups": 0, "sections": 0,
              "submissions": 0, "workbooks": 0}

    async with async_session() as s:
        # пользователи
        for u in data.get("users", []):
            existing = (await s.scalars(
                select(User).where(User.telegram_id == u["telegram_id"]))).first()
            if existing:
                existing.username = u.get("username")
                existing.first_name = u.get("first_name")
                existing.last_name = u.get("last_name")
                existing.role = u.get("role", "student")
            else:
                s.add(User(telegram_id=u["telegram_id"], username=u.get("username"),
                           first_name=u.get("first_name"), last_name=u.get("last_name"),
                           role=u.get("role", "student")))
            if u.get("role") == "curator":
                counts["curators"] += 1

        # группы (по id)
        for g in data.get("groups", []):
            existing = await s.get(Group, g["id"])
            if existing:
                existing.curator_id = g["curator_id"]
                existing.name = g["name"]
                if g.get("token"):
                    existing.token = g["token"]
                if "is_active" in g:
                    existing.is_active = g.get("is_active", True)
                existing.hidden = g.get("hidden", False)
            else:
                s.add(Group(id=g["id"], curator_id=g["curator_id"], name=g["name"],
                            token=g.get("token") or secrets.token_urlsafe(8),
                            is_active=g.get("is_active", True),
                            hidden=g.get("hidden", False),
                            created_at=_dt(g.get("created_at"))))
            counts["groups"] += 1

        # разделы (по id) — нужны для привязки сдач
        for sec in data.get("sections", []):
            existing = await s.get(Section, sec["id"])
            if existing:
                existing.group_id = sec["group_id"]
                existing.curator_id = sec["curator_id"]
                existing.name = sec["name"]
                existing.weeks = sec.get("weeks", 4)
                existing.practices = sec.get("practices", 2)
                existing.period_days = sec.get("period_days", 7)
                existing.start_date = _dt(sec.get("start_date"))
                existing.end_date = _dt(sec.get("end_date"))
                existing.is_active = sec.get("is_active", True)
                existing.deleted = sec.get("deleted", False)
                existing.ended_notified = sec.get("ended_notified", False)
            else:
                s.add(Section(id=sec["id"], group_id=sec["group_id"],
                              curator_id=sec["curator_id"], name=sec["name"],
                              weeks=sec.get("weeks", 4), practices=sec.get("practices", 2),
                              period_days=sec.get("period_days", 7),
                              start_date=_dt(sec.get("start_date")),
                              end_date=_dt(sec.get("end_date")),
                              is_active=sec.get("is_active", True),
                              deleted=sec.get("deleted", False),
                              ended_notified=sec.get("ended_notified", False),
                              created_at=_dt(sec.get("created_at"))))
            counts["sections"] += 1

        # тетради (по serial)
        for w in data.get("workbooks", []):
            existing = (await s.scalars(
                select(Workbook).where(Workbook.serial == w["serial"]))).first()
            if existing:
                existing.topic = w["topic"]
                existing.file_id = w["file_id"]
            else:
                s.add(Workbook(id=w.get("id"), serial=w["serial"], topic=w["topic"],
                               file_id=w["file_id"], created_at=_dt(w.get("created_at"))))
            counts["workbooks"] += 1

        # ученики (по id)
        for st in data.get("students", []):
            existing = await s.get(Student, st["id"])
            if existing:
                existing.user_id = st.get("user_id")
                existing.username = st.get("username")
                existing.group_id = st["group_id"]
                existing.curator_id = st["curator_id"]
                existing.first_name = st["first_name"]
                existing.last_name = st.get("last_name", "")
                existing.is_active = st.get("is_active", True)
                existing.intro_watched = st.get("intro_watched", False)
                existing.welcomed = st.get("welcomed", False)
            else:
                s.add(Student(id=st["id"], user_id=st.get("user_id"),
                              username=st.get("username"), group_id=st["group_id"],
                              curator_id=st["curator_id"], first_name=st["first_name"],
                              last_name=st.get("last_name", ""),
                              is_active=st.get("is_active", True),
                              intro_watched=st.get("intro_watched", False),
                              welcomed=st.get("welcomed", False),
                              added_at=_dt(st.get("added_at"))))
            counts["students"] += 1

        # сдачи (по id) — конспекты И практика
        for sub in data.get("submissions", []):
            existing = await s.get(Submission, sub["id"])
            if existing:
                continue
            s.add(Submission(id=sub["id"], student_id=sub["student_id"],
                             type=sub.get("type", "workbook"),
                             section_id=sub.get("section_id"),
                             week=sub.get("week", 0),
                             pdf_file_id=sub["pdf_file_id"],
                             submitted_name=sub["submitted_name"],
                             submitted_at_utc=_dt(sub.get("submitted_at_utc")),
                             curator_id=sub["curator_id"], is_late=sub.get("is_late", False),
                             late_by_minutes=sub.get("late_by_minutes", 0),
                             hidden_for_curator=sub.get("hidden_for_curator", False),
                             deleted_at=_dt(sub.get("deleted_at")),
                             deleted_by=sub.get("deleted_by")))
            counts["submissions"] += 1

        # настройки (видео-инструкция и т.п.) — по ключу
        for stt in data.get("settings", []):
            existing = await s.get(Setting, stt["key"])
            if existing:
                existing.value = stt.get("value")
            else:
                s.add(Setting(key=stt["key"], value=stt.get("value")))

        # дедлайны
        for d in data.get("deadlines", []):
            existing = (await s.scalars(
                select(Deadline).where(Deadline.curator_id == d["curator_id"]))).first()
            if existing:
                existing.weekday = d["weekday"]
                existing.deadline_time_utc = d["deadline_time_utc"]
                existing.reminders_enabled = d.get("reminders_enabled", True)
            else:
                s.add(Deadline(curator_id=d["curator_id"], group_id=d.get("group_id"),
                               weekday=d["weekday"], deadline_time_utc=d["deadline_time_utc"],
                               reminders_enabled=d.get("reminders_enabled", True)))

        await s.commit()

    await _fix_sequences()
    return counts


async def _fix_sequences() -> None:
    """После вставки строк с явными id обновляем счётчики автоинкремента (для PostgreSQL).

    Иначе следующая новая запись попытается занять уже занятый id и упадёт.
    """
    from sqlalchemy import text
    from database.session import engine
    if engine.dialect.name != "postgresql":
        return  # SQLite сам отслеживает максимум
    tables = ["groups", "sections", "students", "submissions", "workbooks", "deadlines"]
    async with engine.begin() as conn:
        for t in tables:
            try:
                await conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {t}), 1), true)"
                ))
            except Exception:
                pass
