from aiogram import Dispatcher
from . import channel_info, channel_reply, commands


def register_all_handlers(dp: Dispatcher):
    dp.include_router(channel_info.router)
    dp.include_router(channel_reply.router)
    dp.include_router(commands.router)
