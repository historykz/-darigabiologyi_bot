"""Планировщик: напоминания ученикам, итоги куратору, автобэкап."""
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import ADMIN_ID, TZ
from database import crud
from services import backup, roles

# чтобы не слать одно и то же дважды за минуту
_sent: set[str] = set()


def _deadline_dt_local(weekday: int, hhmm_utc: str, ref: datetime) -> datetime:
    """Ближайший дедлайн (в локальном времени) для данного weekday на текущей неделе."""
    h, m = map(int, hhmm_utc.split(":"))
    # перевод времени дедлайна из UTC в локальное
    base_utc = ref.astimezone(timezone.utc).replace(hour=h, minute=m, second=0, microsecond=0)
    local = base_utc.astimezone(TZ)
    # сдвиг к нужному дню недели
    delta = (weekday - local.weekday()) % 7
    return (local + timedelta(days=delta)).replace(second=0, microsecond=0)


async def _non_submitters(curator_id: int, deadline_local: datetime):
    """Ученики куратора без сдачи за неделю до дедлайна."""
    students = await crud.get_students(curator_id=curator_id)
    week_start = (deadline_local - timedelta(days=7)).astimezone(timezone.utc)
    deadline_utc = deadline_local.astimezone(timezone.utc)
    result = []
    for st in students:
        subs = await crud.get_submissions(student_id=st.id)
        recent = [x for x in subs if week_start <= x.submitted_at_utc <= deadline_utc]
        if not recent:
            result.append(st)
    return result


async def _check_reminders(bot: Bot) -> None:
    now = datetime.now(TZ)
    key_min = now.strftime("%Y-%m-%d %H:%M")

    submit_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📤 Сдать РТ", callback_data="student_submit")
    ]])

    for dl in await crud.all_deadlines():
        if not dl.reminders_enabled:
            continue
        deadline_local = _deadline_dt_local(dl.weekday, dl.deadline_time_utc, now)
        if deadline_local.date() != now.date():
            continue

        offsets = {
            "r1": deadline_local - timedelta(hours=4),
            "r2": deadline_local - timedelta(hours=2),
            "r3": deadline_local - timedelta(minutes=30),
        }
        texts = {
            "r1": ("⏰ Напоминание!\nДедлайн сдачи рабочей тетради сегодня в "
                   f"{deadline_local:%H:%M}.\nТы ещё не сдал(а) работу."),
            "r2": ("⚠️ До дедлайна меньше 2 часов!\nТы ещё не сдал(а) рабочую тетрадь.\n"
                   "Сдай сейчас — потом будет поздно."),
            "r3": ("🚨 Осталось около 30 минут!\nПосле дедлайна сдача будет отмечена "
                   "как просроченная.\nСдай прямо сейчас 👇"),
        }

        for tag, t in offsets.items():
            if t.strftime("%Y-%m-%d %H:%M") != key_min:
                continue
            mark = f"{dl.curator_id}:{tag}:{key_min}"
            if mark in _sent:
                continue
            _sent.add(mark)
            for st in await _non_submitters(dl.curator_id, deadline_local):
                if not st.user_id:
                    continue
                try:
                    await bot.send_message(st.user_id, texts[tag], reply_markup=submit_kb)
                except Exception:
                    pass

        # итоговый отчёт куратору через 2 минуты после дедлайна
        report_t = deadline_local + timedelta(minutes=2)
        if report_t.strftime("%Y-%m-%d %H:%M") == key_min:
            mark = f"report:{dl.curator_id}:{key_min}"
            if mark not in _sent:
                _sent.add(mark)
                await _send_report(bot, dl.curator_id, deadline_local)


async def _send_report(bot: Bot, curator_id: int, deadline_local: datetime) -> None:
    groups = await crud.get_groups(curator_id=curator_id)
    week_start = (deadline_local - timedelta(days=7)).astimezone(timezone.utc)
    deadline_utc = deadline_local.astimezone(timezone.utc)
    d_from = (deadline_local - timedelta(days=7)).strftime("%d.%m")
    d_to = deadline_local.strftime("%d.%m")

    for g in groups:
        students = await crud.get_students(curator_id=curator_id, group_id=g.id)
        on_time, late, missing = [], [], []
        for st in students:
            subs = await crud.get_submissions(student_id=st.id)
            recent = [x for x in subs if week_start <= x.submitted_at_utc <= deadline_utc + timedelta(days=1)]
            if not recent:
                missing.append(st)
                continue
            last = recent[0]
            if last.is_late:
                late.append((st, last))
            else:
                on_time.append((st, last))

        lines = [f"📊 Итоги недели {d_from}–{d_to}", f"Группа «{g.name}»", ""]
        lines.append(f"✅ Сдали вовремя ({len(on_time)} из {len(students)}):")
        for st, sub in on_time:
            lines.append(f"  • {st.first_name} {st.last_name} · {roles.to_local(sub.submitted_at_utc):%H:%M}")
        lines.append("")
        lines.append(f"⚠️ Просрочили ({len(late)}):")
        for st, sub in late:
            lines.append(f"  • {st.first_name} {st.last_name} — +{sub.late_by_minutes} мин")
        lines.append("")
        lines.append(f"❌ Не сдали ({len(missing)}):")
        for st in missing:
            lines.append(f"  • {st.first_name} {st.last_name}")

        try:
            await bot.send_message(curator_id, "\n".join(lines))
        except Exception:
            pass


async def _auto_backup(bot: Bot) -> None:
    data = await backup.collect()
    raw = backup.make_json(data)
    fname = f"backup_{datetime.now(TZ):%d.%m.%Y}.json"
    stats = {
        "users": len(data["users"]),
        "subs": len([x for x in data["submissions"] if not x["deleted_at"]]),
        "groups": len(data["groups"]),
    }
    text = (f"💾 Автобэкап выполнен — {datetime.now(TZ):%d.%m.%Y}\n"
            f"   Пользователей: {stats['users']} | Работ: {stats['subs']} | "
            f"Групп: {stats['groups']}")
    try:
        await bot.send_document(ADMIN_ID, BufferedInputFile(raw, fname), caption=text)
    except Exception:
        pass


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=TZ)
    # проверка напоминаний/отчётов каждую минуту
    sched.add_job(_check_reminders, "interval", minutes=1, args=[bot])
    # автобэкап: воскресенье 03:00 по Астане
    sched.add_job(_auto_backup, "cron", day_of_week="sun", hour=3, minute=0, args=[bot])
    return sched
