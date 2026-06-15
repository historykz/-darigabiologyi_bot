"""Определение роли по telegram_id и форматирование времени (UTC+5)."""
from datetime import datetime, timedelta, timezone

from config import ADMIN_ID, TZ
from database import crud

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]


async def get_role(telegram_id: int) -> str:
    """admin / curator / student / unknown.

    Ученик считается учеником ТОЛЬКО при наличии активной записи в students.
    Если куратор удалил его (is_active=False) — доступ закрыт (unknown).
    """
    if telegram_id == ADMIN_ID:
        return "admin"
    user = await crud.get_user_by_tg(telegram_id)
    if user is not None and user.role == "admin":
        return "admin"
    if user is not None and user.role == "curator":
        return "curator"
    student = await crud.get_student_by_tg(telegram_id)  # только активные
    if student is not None:
        return "student"
    return "unknown"


def to_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)


def now_local() -> datetime:
    return datetime.now(TZ)


def _to_local_date(dt) -> datetime:
    """Любой datetime → локальная полночь (начало дня по Астане)."""
    if dt is None:
        return now_local().replace(hour=0, minute=0, second=0, microsecond=0)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    loc = dt.astimezone(TZ)
    return loc.replace(hour=0, minute=0, second=0, microsecond=0)


def section_start_local(section) -> datetime:
    return _to_local_date(getattr(section, "start_date", None) or section.created_at)


def section_end_local(section) -> datetime | None:
    ed = getattr(section, "end_date", None)
    if not ed:
        return None
    loc = _to_local_date(ed)
    # конец дня: 23:59
    return loc.replace(hour=23, minute=59, second=59)


def section_period(section) -> int:
    """Длительность одного периода (недели) в днях."""
    return max(1, getattr(section, "period_days", 7) or 7)


def section_total_weeks(section) -> int:
    """Сколько недель (периодов) всего в разделе по датам."""
    start = section_start_local(section)
    end = section_end_local(section)
    p = section_period(section)
    if end is None:
        return max(1, getattr(section, "weeks", 4) or 4)
    total_days = (end - start).days + 1
    return max(1, (total_days + p - 1) // p)


def section_week_start_local(section, week: int) -> datetime:
    """Начало недели week (1-based) по локальному времени."""
    start = section_start_local(section)
    p = section_period(section)
    return start + timedelta(days=p * (week - 1))


def section_week_end_local(section, week: int) -> datetime:
    """Конец недели (дедлайн): последний день периода 23:59 локально."""
    ws = section_week_start_local(section, week)
    p = section_period(section)
    last_day = ws + timedelta(days=p - 1)
    return last_day.replace(hour=23, minute=59, second=59)


def section_current_week(section) -> int:
    """Текущая неделя: 0 — ещё не начался; 1..N — идёт; >N — все прошли."""
    start = section_start_local(section)
    now = now_local()
    if now < start:
        return 0
    p = section_period(section)
    return (now - start).days // p + 1


def section_deadline_str(section, week: int) -> str:
    """«DD.MM 23:59» — конец недели для показа пользователю."""
    d = section_week_end_local(section, week)
    return f"{d.day:02d}.{d.month:02d} 23:59"


def section_week_range_str(section, week: int) -> str:
    """«DD.MM – DD.MM» — диапазон недели."""
    ws = section_week_start_local(section, week)
    we = section_week_end_local(section, week)
    return f"{ws.day:02d}.{ws.month:02d} – {we.day:02d}.{we.month:02d}"


def section_start_str(section) -> str:
    s = section_start_local(section)
    return f"{s.day:02d}.{s.month:02d}.{s.year}"


def section_end_str(section) -> str:
    e = section_end_local(section)
    if not e:
        return "—"
    return f"{e.day:02d}.{e.month:02d}.{e.year}"


def section_is_week_late(section, week: int) -> tuple[bool, int]:
    """Просрочена ли неделя? Возвращает (просрочено, минут_опоздания)."""
    end = section_week_end_local(section, week)
    now = now_local()
    if now > end:
        return True, int((now - end).total_seconds() // 60)
    return False, 0


def fmt_absolute(dt: datetime) -> str:
    """«25 июня, 14:32»."""
    local = to_local(dt)
    return f"{local.day} {MONTHS[local.month - 1]}, {local:%H:%M}"


def fmt_relative(dt: datetime) -> str:
    """«только что» / «17 минут назад» / «вчера, 21:45» / «25 июня, 14:32»."""
    local = to_local(dt)
    now = datetime.now(TZ)
    diff = (now - local).total_seconds()

    if diff < 60:
        return "только что"
    if diff < 3600:
        m = int(diff // 60)
        return f"{m} {_plural(m, 'минуту', 'минуты', 'минут')} назад"
    if diff < 86400 and now.date() == local.date():
        h = int(diff // 3600)
        return f"{h} {_plural(h, 'час', 'часа', 'часов')} назад"
    # вчера
    yest = (now - now.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds()
    if 0 <= diff <= 86400 + yest and (now.date() - local.date()).days == 1:
        return f"вчера, {local:%H:%M}"
    return fmt_absolute(dt)


def fmt_deadline(dt: datetime) -> str:
    """«25 июня, 14:32 (38 минут назад)» — оба формата сразу."""
    return f"{fmt_absolute(dt)} ({fmt_relative(dt)})"


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    n1 = n % 10
    if 10 < n < 20:
        return many
    if n1 == 1:
        return one
    if 1 < n1 < 5:
        return few
    return many
