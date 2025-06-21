from __future__ import annotations

from datetime import datetime, timedelta
import asyncio
import json
import pathlib
from html import escape
from typing import Final

import asyncpg
from aiogram.enums import ParseMode
from aiogram.types import (
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Message as TgMsg,
)
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.loader import bot, logger
from app.utils.config import settings
from app.utils.db import async_session_maker
from shared.models import (
    FileType,
    Instance,
    Message,
    MessageDirection,
    MessageFile,
    MessageStatus, MessageType,
)
from shared.utils import stringify
from shared import locale as L


# helpers
def _phone(chat_id: str) -> str:
    return chat_id.split("@", 1)[0].lstrip("+")


def _make_kb(inst: Instance, chat_id_wa: str) -> InlineKeyboardMarkup:
    phone, suffix = chat_id_wa.rsplit("@", 1)        # 79991112233, c.us / g.us
    if suffix == "g.us":
        phone += "-g"

    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=phone,
                url=f"https://{settings.WEBHOOK_HOST}/chat/{inst.api_id}/{phone}",
            )
        ]]
    )


def _pretty_caption(msg: Message) -> str:
    name = escape(msg.chat_name or "—")
    txt = escape(msg.text or "—")
    phone = _phone(msg.chat_id)
    return stringify(L.NEW_MESSAGE, name=name, phone=phone, text=txt)


def _tg_file(rec: MessageFile) -> InputFile:
    return FSInputFile(pathlib.Path(rec.file_path), filename=rec.name)


# listener
async def msg_inbox(stop: asyncio.Event) -> None:
    """
    Telegram notifications
    msg_in
    """

    pg = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_db,
        host=settings.postgres_host,
        port=settings.postgres_port,
    )

    async def _handler(_, __, ___, payload: str) -> None:  # noqa: ANN001
        msg_id = json.loads(payload)["msg_id"]

        async with async_session_maker() as db:
            msg: Message | None = await db.scalar(
                select(Message)
                .where(Message.id == msg_id)
                .options(
                    selectinload(Message.instance).selectinload(Instance.telegram_channel),
                    selectinload(Message.files),
                )
            )
            if msg is None:
                logger.error("msg_in: message %s not found", msg_id)
                return

            if msg.is_archived:
                return

            inst: Instance = msg.instance
            try:
                if msg.message_type == MessageType.call:
                    if msg.direction == MessageDirection.inc:
                        sent: TgMsg = await bot.send_message(
                            chat_id=inst.telegram_channel.telegram_id,
                            text=stringify(L.NEW_CALL, name=escape(msg.chat_name or "—"), phone=_phone(msg.chat_id)),
                            parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True,
                            reply_markup=_make_kb(inst, msg.chat_id),
                        )
                        msg.tg_message_id = sent.message_id
                        await db.commit()
                else:
                    if msg.direction == MessageDirection.inc:
                        caption = _pretty_caption(msg)
                    else:
                        caption = escape(msg.text or "—")
                    kb = _make_kb(inst, msg.chat_id)

                    if not msg.files:
                        sent: TgMsg = await bot.send_message(
                            chat_id=inst.telegram_channel.telegram_id,
                            text=caption,
                            parse_mode=ParseMode.MARKDOWN,
                            disable_web_page_preview=True,
                            reply_markup=kb,
                        )
                    else:
                        sent = await _send_with_files(inst, msg, caption, kb)

                    msg.tg_message_id = sent.message_id
                    await db.commit()

                    # auto-reply

                    if msg.direction == MessageDirection.inc and inst.auto_reply and inst.auto_reply_text:
                        exists = await db.scalar(select(Message.id).where(
                            Message.instance_id == inst.id,
                            Message.chat_id == msg.chat_id,
                            Message.is_auto == True,
                            Message.created_at >= datetime.utcnow() - timedelta(hours=settings.AUTO_REPLY_INTERVAL),
                        ).limit(1))

                        if not exists:
                            auto_msg = Message(
                                instance_id=inst.id,
                                chat_id=msg.chat_id,
                                chat_name=msg.chat_name,
                                direction=MessageDirection.out,
                                is_auto=True,
                                text=inst.auto_reply_text,
                                status=MessageStatus.pending,
                                message_type=MessageType.text,
                            )
                            db.add(auto_msg)
                            await db.commit()
            except Exception as exc:
                logger.error("msg_in: send failed → %s", exc)
                await db.execute(
                    update(Message)
                    .where(Message.id == msg.id)
                    .values(status=MessageStatus.error_int)
                )
                await db.commit()

    await pg.add_listener("msg_in", _handler)
    logger.info("LISTEN msg_in — started")

    try:
        await stop.wait()
    finally:
        await pg.remove_listener("msg_in", _handler)
        await pg.close()
        logger.info("LISTEN msg_in — stopped")


async def _send_with_files(
        inst: Instance,
        msg: Message,
        caption: str,
        kb: InlineKeyboardMarkup | None,
):
    """
    Selects suitable GAPI method for file type.
    Creates caption ONLY for first file
    """
    first, *rest = msg.files
    chat_id = inst.telegram_channel.telegram_id

    # single media
    if len(msg.files) == 1:
        if first.file_type is FileType.image:
            return await bot.send_photo(
                chat_id, _tg_file(first),
                caption=caption, parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb,
            )
        if first.file_type is FileType.video:
            return await bot.send_video(
                chat_id, _tg_file(first),
                caption=caption, parse_mode=ParseMode.MARKDOWN,
                supports_streaming=True,
                reply_markup=kb,
            )
        if first.file_type is FileType.audio:
            return await bot.send_audio(
                chat_id, _tg_file(first),
                caption=caption, parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb,
            )

    # doc or multiple files
    cap = caption
    sent_msg: TgMsg | None = None
    for rec in msg.files:
        sent_msg = await bot.send_document(
            chat_id, _tg_file(rec),
            caption=cap, parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        cap = None
    return sent_msg
