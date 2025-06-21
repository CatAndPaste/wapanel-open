from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Final

import asyncpg
from aiohttp import FormData
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aiogram.types.reaction_type_emoji import ReactionTypeEmoji

from app.green_api.exceptions import GreenAPIError
from app.loader import app, bot, logger
from app.utils.config import settings
from app.utils.db import async_session_maker
from app.utils.messages import notify_send_error
from shared.models import (
    Message, MessageFile,
    MessageStatus, FileType, Instance,
)

# endpoint names
_GREEN_ENDPOINT: Final[str] = "sendFileByUpload"


# listener
async def msg_outbox(stop: asyncio.Event) -> None:
    """
    Sends message to GAPI
    msg_out
    """

    pg = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_db,
        host=settings.postgres_host,
        port=settings.postgres_port,
    )

    async def handle(_, __, ___, payload: str) -> None:
        msg_id = json.loads(payload)["msg_id"]

        async with async_session_maker() as db:
            msg: Message | None = await db.scalar(
                select(Message)
                .where(Message.id == msg_id)
                .options(selectinload(Message.files),
                         selectinload(Message.instance).selectinload(Instance.telegram_channel))
            )
            if msg is None:
                logger.error("msg_out: message %s not found", msg_id)
                return

            if msg.is_archived or not msg.from_app:
                return

            inst = msg.instance
            chat_id = msg.chat_id

            try:
                async with app["client_manager"].get_client(inst.api_id) as client:
                    if msg.files:
                        resp = await _send_files(client, chat_id, msg)
                    else:
                        resp = await client.send_message(chat_id=chat_id, text=msg.text or "")

                ok = await _mark_wa_id(db, msg, resp)
                await _mark_status(db, msg, MessageStatus.sent, ok=ok)

            except GreenAPIError as e:
                await _mark_status(db, msg, MessageStatus.error_api, ok=False)
                await notify_send_error(db, msg, f"ошибка API ({e})")
                logger.error("API error while sending: %s", e)

            except Exception as e:                                # noqa: BLE001
                await _mark_status(db, msg, MessageStatus.error_int, ok=False)
                await notify_send_error(db, msg, "внутренняя ошибка")
                logger.exception("Internal error while sending msg")  # stack-trace

    await pg.add_listener("msg_out", handle)
    logger.info("LISTEN msg_out — started")

    try:
        await stop.wait()
    finally:
        await pg.remove_listener("msg_out", handle)
        await pg.close()
        logger.info("LISTEN msg_out — stopped")


# helpers
async def _mark_status(db, msg: Message, st: MessageStatus, *, ok: bool) -> None:
    """Updates status + channel reaction (if sent from there)"""
    msg.status = st
    await db.commit()

    if msg.tg_message_id is None:
        return

    emoji = "👍" if ok else "😡"
    try:
        await bot.set_message_reaction(
            chat_id=msg.instance.telegram_channel.telegram_id,
            message_id=msg.tg_message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception as e:               # noqa: BLE001
        logger.warning("cannot set reaction: %s", e)


async def _send_files(client, chat_id: str, msg: Message) -> None:
    """
    One-by-one file transfer
    First file should have caption if exists
    """
    caption = msg.text or ""
    resp = None
    for file_rec in msg.files:
        # -> bytes for GAPI
        data = Path(file_rec.file_path).read_bytes()

        resp = await client.send_file(
            chat_id=chat_id,
            file_bytes=data,
            filename=file_rec.name,
            mime=file_rec.mime,
            caption=caption,
        )
        caption = ""

    return resp


async def _mark_wa_id(db: AsyncSession, msg: Message, resp: dict) -> bool:
    wa_id = (resp or {}).get("idMessage")
    if not wa_id:
        logger.error(f"send failed: {json.dumps(resp)}")
        return False

    if msg.wa_message_id:
        return msg.wa_message_id == wa_id

    msg.wa_message_id = wa_id
    db.add(msg)
    await db.commit()
    return True
