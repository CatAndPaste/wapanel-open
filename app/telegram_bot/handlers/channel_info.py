from aiogram import Router, F
from aiogram.enums import ChatMemberStatus
from aiogram.types import Message, ChatMemberUpdated
from aiogram.enums.chat_type import ChatType
from sqlalchemy import select

from app.loader import logger
from shared.crud.channel import get_or_create as get_or_create_channel
from app.utils.db import async_session_maker
from shared.models import Instance
from shared.utils import stringify
from shared import locale as L

router = Router()


@router.my_chat_member()
async def on_my_chat_member_update(update: ChatMemberUpdated):
    logger.info(f"INVITE HANDLER TRIGGERED: {update.chat.type} - {update.old_chat_member.status} - "
                f"{update.new_chat_member.status}")
    # channels only
    if update.chat.type != ChatType.CHANNEL:
        return

    old_status: ChatMemberStatus = update.old_chat_member.status
    new_status: ChatMemberStatus = update.new_chat_member.status

    tg_channel_id = update.chat.id
    tg_title = update.chat.title or ""
    tg_username = getattr(update.chat, "username", None)

    async with async_session_maker() as session:
        channel_rec = await get_or_create_channel(
            session,
            telegram_id=tg_channel_id,
            defaults={}
        )

        # bot added to channel
        if old_status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED) \
                and new_status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
            logger.info("BOT ADDED")
            # updates the name
            channel_rec.name = tg_title

            # Attempt to generate invite link
            try:
                link = await update.bot.create_chat_invite_link(chat_id=tg_channel_id)
                channel_rec.url = link.invite_link
            except Exception:
                # fallback
                if tg_username:
                    channel_rec.url = f"https://t.me/{tg_username}"
                else:
                    channel_rec.url = f"https://t.me/c/{str(tg_channel_id)}"

            channel_rec.is_active = True

            session.add(channel_rec)
            await session.commit()

            res = await session.execute(
                select(Instance.api_id).where(Instance.telegram_channel_id == channel_rec.id)
            )
            api_ids = [row[0] for row in res.all()]

            text = stringify(L.ON_JOIN_DEFAULT, channel_id=tg_channel_id)
            if api_ids:
                ids_list = ", ".join(str(i) for i in api_ids)
                text = stringify(L.ON_JOIN_INSTANCE, instances=ids_list)

            await update.bot.send_message(
                chat_id=tg_channel_id,
                text=text,
                parse_mode="Markdown",
            )
            return

        elif old_status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR) \
                and new_status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
            logger.info("BOT REMOVED")
            channel_rec.name = None
            channel_rec.url = None
            channel_rec.is_active = False
            session.add(channel_rec)
            await session.commit()
            print(f"Бот удалён из канала: {tg_title or tg_channel_id}")
            return


@router.channel_post(F.new_chat_title)
async def on_channel_title_change(msg: Message):
    logger.info("TITLE CHANGE TRIGGERED")
    # channel only
    if msg.chat.type != ChatType.CHANNEL:
        return

    tg_channel_id = msg.chat.id
    new_title = msg.new_chat_title

    async with async_session_maker() as session:
        channel_rec = await get_or_create_channel(
            session,
            telegram_id=tg_channel_id,
            defaults={}
        )
        channel_rec.name = new_title

        if msg.chat.username:
            channel_rec.url = f"https://t.me/{msg.chat.username}"

        session.add(channel_rec)
        await session.commit()

        logger.info(f"new name: {channel_rec.name} | {channel_rec.url}")
