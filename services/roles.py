"""Определение роли по telegram_id и форматирование времени (UTC+5)."""
from datetime import datetime, timezone

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
