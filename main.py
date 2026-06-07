"""Точка входа. Регистрация роутеров, запуск планировщика и polling."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from database.session import init_db
from handlers import admin, common, curator, student
from services.scheduler import setup_scheduler

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("bot")


async def main() -> None:
    await init_db()

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=None))
    dp = Dispatcher(storage=MemoryStorage())

    # Порядок важен: роли — первыми, common с catch-all — последним.
    dp.include_router(admin.router)
    dp.include_router(curator.router)
    dp.include_router(student.router)
    dp.include_router(common.router)

    scheduler = setup_scheduler(bot)
    scheduler.start()
    logger.info("Scheduler started")

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot started — polling")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
