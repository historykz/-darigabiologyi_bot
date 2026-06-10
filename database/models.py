"""SQLAlchemy 2.0 модели. Все временны́е поля — в UTC."""
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="student")  # admin/curator/student
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    curator_id: Mapped[int] = mapped_column(BigInteger, index=True)  # telegram_id куратора
    name: Mapped[str] = mapped_column(String(128))
    token: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    students: Mapped[list["Student"]] = relationship(back_populates="group")


class Student(Base):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)  # telegram_id
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    curator_id: Mapped[int] = mapped_column(BigInteger, index=True)
    first_name: Mapped[str] = mapped_column(String(128))
    last_name: Mapped[str] = mapped_column(String(128), default="")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    intro_watched: Mapped[bool] = mapped_column(Boolean, default=False)
    welcomed: Mapped[bool] = mapped_column(Boolean, default=False)

    group: Mapped["Group"] = relationship(back_populates="students")


class Workbook(Base):
    __tablename__ = "workbooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    serial: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    topic: Mapped[str] = mapped_column(String(256))
    file_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Section(Base):
    """Раздел (предмет) внутри группы: Анатомия, Ботаника… с числом недель."""
    __tablename__ = "sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    curator_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(128))
    weeks: Mapped[int] = mapped_column(Integer, default=4)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    ended_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Submission(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    type: Mapped[str] = mapped_column(String(32), default="workbook")
    section_id: Mapped[int | None] = mapped_column(ForeignKey("sections.id"), nullable=True, index=True)
    week: Mapped[int] = mapped_column(Integer, default=0)
    pdf_file_id: Mapped[str] = mapped_column(Text)
    submitted_name: Mapped[str] = mapped_column(String(256))
    submitted_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    curator_id: Mapped[int] = mapped_column(BigInteger, index=True)
    is_late: Mapped[bool] = mapped_column(Boolean, default=False)
    late_by_minutes: Mapped[int] = mapped_column(Integer, default=0)
    # скрыто из списка куратора, но у ученика работа остаётся
    hidden_for_curator: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Deadline(Base):
    __tablename__ = "deadlines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    curator_id: Mapped[int] = mapped_column(BigInteger, index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"), nullable=True)
    weekday: Mapped[int] = mapped_column(Integer, default=4)  # 0=пн … 6=вс
    deadline_time_utc: Mapped[str] = mapped_column(String(8), default="18:59")  # HH:MM в UTC
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    """key-value хранилище (ссылка на Google Sheets и пр.)."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
