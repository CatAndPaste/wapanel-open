from aiogram import Bot
from aiogram.enums.chat_member_status import ChatMemberStatus
from aiogram.enums.chat_type import ChatType
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.crud.channel import get_or_create as get_or_create_channel
from shared.models import TelegramChannel
from app.utils.db import async_session_maker


async def sync_channel_record(
    bot: Bot,
    telegram_channel_id: int,
    *,
    session: AsyncSession
) -> None:
    # Ger or create channel obj
    chan = await get_or_create_channel(session, telegram_id=telegram_channel_id, defaults={})

    try:
        # bot status in chat
        member = await bot.get_chat_member(chat_id=telegram_channel_id, user_id=(await bot.get_me()).id)
        # chat info
        chat = await bot.get_chat(chat_id=telegram_channel_id)
    except Exception:
        # unavailable
        chan.is_active = False
        chan.name = None
        chan.url = None
    else:
        # bot is in the channel (member/admin)
        if member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
            chan.is_active = True
            # saves new name
            chan.name = chat.title or chan.name
            # URL: invite_link or username
            if member.status is ChatMemberStatus.ADMINISTRATOR:
                try:
                    link = await bot.create_chat_invite_link(chat_id=telegram_channel_id)
                    chan.url = link.invite_link
                except Exception:
                    pass
            # fallback
            if not chan.url:
                if chat.username:
                    chan.url = f"https://t.me/{chat.username}"
                else:
                    chan.url = f"https://t.me/c/{str(telegram_channel_id)}"
        else:
            # бот blocked
            chan.is_active = False
            chan.name = None
            chan.url = None

    session.add(chan)
    await session.commit()
