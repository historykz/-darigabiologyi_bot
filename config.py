"""Конфигурация бота. Все секреты — из переменных окружения."""
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Railway отдаёт DATABASE_URL автоматически. Приводим к asyncpg-драйверу.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///bot.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# Часовой пояс отображения — Астана (UTC+5)
TZ = ZoneInfo("Asia/Almaty")

# Google Sheets (опционально). Путь к service-account JSON.
GOOGLE_CREDS_FILE = os.getenv("GOOGLE_CREDS_FILE", "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в переменных окружения")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID не задан в переменных окружения")
