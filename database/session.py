"""Асинхронная сессия БД."""
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import DATABASE_URL
from database.models import Base

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Создаёт таблицы и добавляет недостающие колонки (лёгкая миграция)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_columns)


def _ensure_columns(sync_conn) -> None:
    """Idempotent ALTER TABLE для колонок, добавленных после первого деплоя."""
    insp = inspect(sync_conn)
    if "submissions" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("submissions")}
    if "hidden_for_curator" not in cols:
        # bool как 0/1 совместимо и с PostgreSQL, и со SQLite
        sync_conn.execute(text(
            "ALTER TABLE submissions ADD COLUMN hidden_for_curator BOOLEAN DEFAULT FALSE"
        ))
