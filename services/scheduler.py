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
    """Ученики куратора без сдачи за неделю до дедлайна (один запрос на всех)."""
    students = await crud.get_students(curator_id=curator_id)
    week_start = (deadline_local - timedelta(days=7)).astimezone(timezone.utc)
    deadline_utc = deadline_local.astimezone(timezone.utc)
    submitted = await crud.submitted_student_ids(curator_id, week_start, deadline_utc)
    return [st for st in students if st.id not in submitted]


async def _check_reminders(bot: Bot) -> None:
    now = datetime.now(TZ)
    day_key = now.strftime("%Y-%m-%d")

    # не даём множеству меток расти бесконечно
    if len(_sent) > 20000:
        _sent.clear()

    submit_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📤 Сдать РТ", callback_data="student_submit")
    ]])

    def due(t: datetime) -> bool:
        # сработать, если момент наступил в последние ~2 минуты (устойчиво к дрейфу)
        delta = (now - t).total_seconds()
        return 0 <= delta < 120

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
            "r1": ("⏰ Привет! Небольшое напоминание 🙂\n"
                   f"Сегодня в {deadline_local:%H:%M} дедлайн сдачи рабочей тетради, "
                   "а ты ещё не сдал(а). Время есть — справишься! 💪"),
            "r2": ("⚠️ До дедлайна меньше 2 часов!\nТы ещё не сдал(а) рабочую тетрадь.\n"
                   "Лучше сдать сейчас, чтобы не переживать 🙏"),
            "r3": ("🚨 Осталось около 30 минут!\nПосле дедлайна сдача будет отмечена "
                   "как просроченная.\nСдай прямо сейчас 👇"),
        }

        for tag, t in offsets.items():
            if not due(t):
                continue
            mark = f"{dl.curator_id}:{tag}:{day_key}"  # один раз за этот дедлайн
            if mark in _sent:
                continue
            _sent.add(mark)

            non_sub = await _non_submitters(dl.curator_id, deadline_local)
            non_sub_ids = {st.id for st in non_sub}
            for st in non_sub:
                if not st.user_id:
                    continue
                try:
                    await bot.send_message(st.user_id, texts[tag], reply_markup=submit_kb)
                except Exception:
                    pass

            # тем, кто УЖЕ сдал — один дружелюбный месседж на первом напоминании, без спама
            if tag == "r1":
                students = await crud.get_students(curator_id=dl.curator_id)
                for st in students:
                    if st.id in non_sub_ids or not st.user_id:
                        continue
                    try:
                        await bot.send_message(
                            st.user_id,
                            "🌟 Молодец, ты уже сдал(а) рабочую тетрадь на этой неделе!\n"
                            "Не забудь отправить все домашние задания куратору до дедлайна. "
                            "Хорошего дня! 😊",
                        )
                    except Exception:
                        pass

        # итоговый отчёт куратору через 2 минуты после дедлайна
        if due(deadline_local + timedelta(minutes=2)):
            mark = f"report:{dl.curator_id}:{day_key}"
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
        # все сдачи группы за период — одним запросом
        subs = await crud.get_submissions(curator_id=curator_id, group_id=g.id)
        last_by_student = {}
        for sub in subs:  # desc по дате
            if week_start <= sub.submitted_at_utc <= deadline_utc + timedelta(days=1):
                last_by_student.setdefault(sub.student_id, sub)

        on_time, late, missing = [], [], []
        for st in students:
            last = last_by_student.get(st.id)
            if last is None:
                missing.append(st)
            elif last.is_late:
                late.append((st, last))
            else:
                on_time.append((st, last))

        lines = [f"📊 Итоги недели {d_from}–{d_to}", f"Группа «{g.name}»", ""]
        lines.append(f"✅ Сдали вовремя: {len(on_time)} из {len(students)}")
        lines.append(f"⚠️ Просрочили: {len(late)}")
        for st, sub in late[:50]:
            lines.append(f"  • {st.first_name} {st.last_name} — +{sub.late_by_minutes} мин")
        lines.append(f"❌ Не сдали: {len(missing)}")
        for st in missing[:50]:
            lines.append(f"  • {st.first_name} {st.last_name}")
        if len(missing) > 50 or len(late) > 50:
            lines.append("… полный список — в разделе «📥 Кто сдал».")

        try:
            await bot.send_message(curator_id, "\n".join(lines)[:4000])
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


async def _section_end_notices(bot: Bot) -> None:
    """Сообщает кураторам, что запланированные недели раздела прошли — пора создать новый."""
    for sec in await crud.sections_pending_end_notice():
        g = await crud.get_group(sec.group_id)
        try:
            await bot.send_message(
                sec.curator_id,
                f"📚 Раздел «{sec.name}» (группа «{g.name if g else '—'}») — "
                f"{sec.weeks} недель прошли.\n"
                "Можно создать новый раздел: «📥 Кто сдал» → выбери группу → «➕ Добавить раздел».\n"
                "Старые сдачи никуда не денутся — они сохранены в этом разделе.")
        except Exception:
            pass
        await crud.mark_section_notified(sec.id)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    sched = AsyncIOScheduler(timezone=TZ)
    # проверка напоминаний/отчётов каждую минуту
    sched.add_job(_check_reminders, "interval", minutes=1, args=[bot])
    # напоминание о завершении разделов — раз в час
    sched.add_job(_section_end_notices, "interval", hours=1, args=[bot])
    # автобэкап: воскресенье 03:00 по Астане
    sched.add_job(_auto_backup, "cron", day_of_week="sun", hour=3, minute=0, args=[bot])
    return sched
