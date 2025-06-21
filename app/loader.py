from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

from app.telegram_bot.middleware.admin_rpc import check_admin_token
from app.utils.config import settings
from shared.logger import get_logger

# logger
logger = get_logger(__name__)

# deps
storage = MemoryStorage()
bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher(storage=storage)
app: web.Application = web.Application(middlewares=[check_admin_token])


__all__ = ("logger", "bot", "dp", "app")

