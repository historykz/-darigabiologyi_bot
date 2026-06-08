"""CRUD-операции. Вся работа с БД проходит здесь."""
import secrets
from datetime import datetime, timezone

from sqlalchemy import func, select, update

from database.models import (
    Deadline,
    Group,
    Setting,
    Student,
    Submission,
    User,
    Workbook,
    utcnow,
)
from database.session import async_session


# ─── ПОЛЬЗОВАТЕЛИ ───────────────────────────────────────────────

async def get_user_by_tg(telegram_id: int) -> User | None:
    async with async_session() as s:
        return (await s.scalars(select(User).where(User.telegram_id == telegram_id))).first()


async def upsert_user(telegram_id: int, username: str | None,
                      first_name: str | None, last_name: str | None,
                      role: str | None = None) -> User:
    async with async_session() as s:
        user = (await s.scalars(select(User).where(User.telegram_id == telegram_id))).first()
        if user is None:
            user = User(telegram_id=telegram_id, username=username,
                        first_name=first_name, last_name=last_name,
                        role=role or "student")
            s.add(user)
        else:
            if username is not None:
                user.username = username
            if first_name is not None:
                user.first_name = first_name
            if last_name is not None:
                user.last_name = last_name
            if role is not None:
                user.role = role
        await s.commit()
        await s.refresh(user)
        return user


async def set_role(telegram_id: int, role: str) -> None:
    async with async_session() as s:
        await s.execute(update(User).where(User.telegram_id == telegram_id).values(role=role))
        await s.commit()


async def get_curators() -> list[User]:
    async with async_session() as s:
        return list((await s.scalars(select(User).where(User.role == "curator"))).all())


# ─── ГРУППЫ ─────────────────────────────────────────────────────

async def create_group(curator_id: int, name: str) -> Group:
    async with async_session() as s:
        g = Group(curator_id=curator_id, name=name, token=secrets.token_urlsafe(8))
        s.add(g)
        await s.commit()
        await s.refresh(g)
    # дефолтный дедлайн (пятница 23:59 Астана = 18:59 UTC), чтобы напоминания работали сразу
    await ensure_default_deadline(curator_id)
    return g


async def get_group_by_token(token: str) -> Group | None:
    async with async_session() as s:
        return (await s.scalars(select(Group).where(Group.token == token))).first()


async def ensure_default_deadline(curator_id: int) -> None:
    existing = await get_deadline(curator_id)
    if existing is None:
        await upsert_deadline(curator_id, weekday=4, deadline_time_utc="18:59",
                              reminders_enabled=True)


async def get_groups(curator_id: int | None = None) -> list[Group]:
    async with async_session() as s:
        q = select(Group).where(Group.is_active == True)  # noqa: E712
        if curator_id is not None:
            q = q.where(Group.curator_id == curator_id)
        return list((await s.scalars(q.order_by(Group.id))).all())


async def get_group(group_id: int) -> Group | None:
    async with async_session() as s:
        return await s.get(Group, group_id)


async def delete_group(group_id: int) -> int:
    """Архивирует группу и всех её активных учеников. История сдач сохраняется.

    Возвращает число удалённых учеников.
    """
    async with async_session() as s:
        g = await s.get(Group, group_id)
        if g is None:
            return 0
        g.is_active = False
        res = await s.execute(
            update(Student).where(Student.group_id == group_id, Student.is_active == True)
            .values(is_active=False)
        )
        await s.commit()
        return res.rowcount or 0


async def count_students(group_id: int) -> int:
    async with async_session() as s:
        return (await s.scalar(
            select(func.count(Student.id)).where(
                Student.group_id == group_id, Student.is_active == True)
        )) or 0


# ─── УЧЕНИКИ ────────────────────────────────────────────────────

async def add_student(first_name: str, last_name: str, username: str | None,
                      user_id: int | None, group_id: int, curator_id: int) -> Student:
    async with async_session() as s:
        st = Student(first_name=first_name, last_name=last_name, username=username,
                     user_id=user_id, group_id=group_id, curator_id=curator_id)
        s.add(st)
        await s.commit()
        await s.refresh(st)
        return st


async def add_students_bulk(items: list[dict], group_id: int, curator_id: int) -> int:
    """Добавляет сразу много учеников за одну транзакцию (для больших списков).

    items: [{first, last, username, user_id}, ...]. Возвращает число добавленных.
    """
    async with async_session() as s:
        objs = [
            Student(first_name=it["first"], last_name=it.get("last", ""),
                    username=it.get("username"), user_id=it.get("user_id"),
                    group_id=group_id, curator_id=curator_id)
            for it in items
        ]
        s.add_all(objs)
        await s.commit()
        return len(objs)


async def get_students(curator_id: int | None = None,
                       group_id: int | None = None) -> list[Student]:
    async with async_session() as s:
        q = select(Student).where(Student.is_active == True)
        if curator_id is not None:
            q = q.where(Student.curator_id == curator_id)
        if group_id is not None:
            q = q.where(Student.group_id == group_id)
        return list((await s.scalars(q.order_by(Student.id))).all())


async def get_student(student_id: int) -> Student | None:
    async with async_session() as s:
        return await s.get(Student, student_id)


async def get_student_by_tg(telegram_id: int) -> Student | None:
    async with async_session() as s:
        return (await s.scalars(
            select(Student).where(Student.user_id == telegram_id, Student.is_active == True)
        )).first()


async def bind_student_by_username(username: str, telegram_id: int) -> Student | None:
    """Привязывает Telegram ID к ученику, добавленному ранее по @username.

    Вызывается при /start ученика: если куратор добавил его по @username,
    то user_id был пустым — теперь подставляем реальный ID, и бот его узнаёт.
    """
    if not username:
        return None
    uname = username.lstrip("@").lower()
    async with async_session() as s:
        # сначала точное совпадение по username, у которого ещё нет user_id
        st = (await s.scalars(
            select(Student).where(
                func.lower(Student.username) == uname,
                Student.is_active == True,
            )
        )).first()
        if st is None:
            return None
        if st.user_id is None or st.user_id != telegram_id:
            st.user_id = telegram_id
            await s.commit()
            await s.refresh(st)
        return st


async def soft_delete_student(student_id: int) -> None:
    async with async_session() as s:
        await s.execute(update(Student).where(Student.id == student_id).values(is_active=False))
        await s.commit()


async def purge_student_submissions(student_id: int, by: int) -> int:
    """Удаляет (soft) все работы ученика — при удалении из группы."""
    async with async_session() as s:
        res = await s.execute(
            update(Submission).where(
                Submission.student_id == student_id, Submission.deleted_at.is_(None)
            ).values(deleted_at=utcnow(), deleted_by=by)
        )
        await s.commit()
        return res.rowcount or 0


async def set_intro_watched(telegram_id: int) -> None:
    async with async_session() as s:
        await s.execute(update(Student).where(
            Student.user_id == telegram_id, Student.is_active == True  # noqa: E712
        ).values(intro_watched=True))
        await s.commit()


async def mark_welcomed(student_id: int) -> None:
    async with async_session() as s:
        await s.execute(update(Student).where(Student.id == student_id).values(welcomed=True))
        await s.commit()


async def search_students(curator_id: int, query: str) -> list[Student]:
    """Поиск учеников куратора по части имени/фамилии/username (регистронезависимо)."""
    q = f"%{query.strip()}%"
    async with async_session() as s:
        return list((await s.scalars(
            select(Student).where(
                Student.curator_id == curator_id,
                Student.is_active == True,  # noqa: E712
            ).where(
                Student.first_name.ilike(q)
                | Student.last_name.ilike(q)
                | func.coalesce(Student.username, "").ilike(q)
            ).limit(30)
        )).all())


async def broadcast_targets(curator_id: int | None = None) -> list[int]:
    """user_id учеников, которым можно написать (они уже запускали бота).

    curator_id=None → все ученики системы (для админа).
    """
    async with async_session() as s:
        q = select(Student.user_id).where(
            Student.is_active == True,  # noqa: E712
            Student.user_id.is_not(None),
        )
        if curator_id is not None:
            q = q.where(Student.curator_id == curator_id)
        return [uid for uid in (await s.scalars(q)).all() if uid]


# ─── РАБОЧИЕ ТЕТРАДИ ────────────────────────────────────────────

async def next_workbook_serial() -> int:
    async with async_session() as s:
        mx = await s.scalar(select(func.max(Workbook.serial)))
        return (mx or 0) + 1


async def add_workbook(serial: int, topic: str, file_id: str) -> Workbook:
    async with async_session() as s:
        wb = Workbook(serial=serial, topic=topic, file_id=file_id)
        s.add(wb)
        await s.commit()
        await s.refresh(wb)
        return wb


async def get_workbook(wb_id: int) -> Workbook | None:
    async with async_session() as s:
        return await s.get(Workbook, wb_id)


async def delete_workbook(wb_id: int) -> bool:
    """Полностью удаляет рабочую тетрадь (если загружена по ошибке)."""
    async with async_session() as s:
        wb = await s.get(Workbook, wb_id)
        if wb is None:
            return False
        await s.delete(wb)
        await s.commit()
        return True


async def get_workbooks() -> list[Workbook]:
    async with async_session() as s:
        return list((await s.scalars(select(Workbook).order_by(Workbook.serial))).all())


async def get_workbook_by_serial(serial: int) -> Workbook | None:
    async with async_session() as s:
        return (await s.scalars(select(Workbook).where(Workbook.serial == serial))).first()


# ─── СДАЧИ ──────────────────────────────────────────────────────

async def add_submission(student_id: int, pdf_file_id: str, submitted_name: str,
                         curator_id: int, is_late: bool = False,
                         late_by_minutes: int = 0) -> Submission:
    async with async_session() as s:
        sub = Submission(student_id=student_id, pdf_file_id=pdf_file_id,
                         submitted_name=submitted_name, curator_id=curator_id,
                         is_late=is_late, late_by_minutes=late_by_minutes)
        s.add(sub)
        await s.commit()
        await s.refresh(sub)
        return sub


async def get_submissions(student_id: int | None = None,
                          curator_id: int | None = None,
                          group_id: int | None = None) -> list[Submission]:
    async with async_session() as s:
        q = select(Submission).where(Submission.deleted_at.is_(None))
        if student_id is not None:
            q = q.where(Submission.student_id == student_id)
        if curator_id is not None:
            # личный список куратора — скрытые им записи не показываем
            q = q.where(Submission.curator_id == curator_id,
                        Submission.hidden_for_curator == False)  # noqa: E712
        if group_id is not None:
            sub_ids = select(Student.id).where(Student.group_id == group_id)
            q = q.where(Submission.student_id.in_(sub_ids))
        return list((await s.scalars(q.order_by(Submission.submitted_at_utc.desc()))).all())


async def soft_delete_submission(sub_id: int, by: int) -> None:
    async with async_session() as s:
        await s.execute(update(Submission).where(Submission.id == sub_id)
                        .values(deleted_at=utcnow(), deleted_by=by))
        await s.commit()


async def hide_submission_for_curator(sub_id: int) -> None:
    """Убрать одну запись из списка куратора. У ученика работа остаётся."""
    async with async_session() as s:
        await s.execute(update(Submission).where(Submission.id == sub_id)
                        .values(hidden_for_curator=True))
        await s.commit()


async def hide_all_for_curator(curator_id: int, group_id: int | None = None) -> int:
    """Очистить весь список куратора (или одну группу). У учеников всё сохраняется.

    Возвращает число скрытых записей.
    """
    async with async_session() as s:
        q = select(Submission).where(
            Submission.curator_id == curator_id,
            Submission.hidden_for_curator == False,  # noqa: E712
            Submission.deleted_at.is_(None),
        )
        if group_id is not None:
            sub_ids = select(Student.id).where(Student.group_id == group_id)
            q = q.where(Submission.student_id.in_(sub_ids))
        rows = list((await s.scalars(q)).all())
        for r in rows:
            r.hidden_for_curator = True
        await s.commit()
        return len(rows)


async def get_submission(sub_id: int) -> Submission | None:
    async with async_session() as s:
        return await s.get(Submission, sub_id)


async def submitted_student_ids(curator_id: int, since_utc, until_utc) -> set[int]:
    """Один запрос: id учеников куратора, сдавших в окне [since, until]."""
    async with async_session() as s:
        rows = await s.scalars(
            select(Submission.student_id).where(
                Submission.curator_id == curator_id,
                Submission.deleted_at.is_(None),
                Submission.submitted_at_utc >= since_utc,
                Submission.submitted_at_utc <= until_utc,
            )
        )
        return set(rows.all())


# ─── ДЕДЛАЙНЫ ───────────────────────────────────────────────────

async def get_deadline(curator_id: int) -> Deadline | None:
    async with async_session() as s:
        return (await s.scalars(
            select(Deadline).where(Deadline.curator_id == curator_id)
        )).first()


async def upsert_deadline(curator_id: int, weekday: int | None = None,
                          deadline_time_utc: str | None = None,
                          reminders_enabled: bool | None = None) -> Deadline:
    async with async_session() as s:
        dl = (await s.scalars(select(Deadline).where(Deadline.curator_id == curator_id))).first()
        if dl is None:
            dl = Deadline(curator_id=curator_id)
            s.add(dl)
        if weekday is not None:
            dl.weekday = weekday
        if deadline_time_utc is not None:
            dl.deadline_time_utc = deadline_time_utc
        if reminders_enabled is not None:
            dl.reminders_enabled = reminders_enabled
        await s.commit()
        await s.refresh(dl)
        return dl


async def all_deadlines() -> list[Deadline]:
    async with async_session() as s:
        return list((await s.scalars(select(Deadline))).all())


# ─── НАСТРОЙКИ ──────────────────────────────────────────────────

async def get_setting(key: str) -> str | None:
    async with async_session() as s:
        st = await s.get(Setting, key)
        return st.value if st else None


async def set_setting(key: str, value: str) -> None:
    async with async_session() as s:
        st = await s.get(Setting, key)
        if st is None:
            s.add(Setting(key=key, value=value))
        else:
            st.value = value
        await s.commit()


# ─── СТАТИСТИКА ─────────────────────────────────────────────────

async def global_stats() -> dict:
    async with async_session() as s:
        curators = await s.scalar(select(func.count(User.id)).where(User.role == "curator"))
        students = await s.scalar(select(func.count(Student.id)).where(Student.is_active == True))
        groups = await s.scalar(select(func.count(Group.id)))
        subs = await s.scalar(select(func.count(Submission.id)).where(Submission.deleted_at.is_(None)))
        wbs = await s.scalar(select(func.count(Workbook.id)))
        return {"curators": curators or 0, "students": students or 0,
                "groups": groups or 0, "submissions": subs or 0, "workbooks": wbs or 0}
