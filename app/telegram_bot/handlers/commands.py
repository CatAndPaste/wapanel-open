from aiogram import types, Router
from aiogram.filters import StateFilter, Command
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import User
from shared import locale as L
from shared.utils import stringify

router = Router()


@router.message(Command("id", "start"))
async def generic_handler(message: types.Message, session: AsyncSession):
    await message.reply(stringify(L.ID_RESPONSE, tg_id=message.from_user.id), parse_mode="Markdown")


@router.message()
async def generic_handler(message: types.Message, session: AsyncSession):
    await message.reply(L.DEFAULT_RESPONSE, parse_mode="Markdown")

