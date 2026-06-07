"""Фильтр по роли — чтобы каждый роутер реагировал только на свою роль."""
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from services import roles


class RoleFilter(BaseFilter):
    def __init__(self, role: str):
        self.role = role

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        uid = event.from_user.id
        return (await roles.get_role(uid)) == self.role
