from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Callable, Awaitable

from aiohttp import ClientSession, web
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.green_api.green_msg import payload_to_msg
from app.loader import app, bot, logger
from app.utils.db import async_session_maker
from app.utils.messages import notify_send_error
from shared.crud.conversations import get_or_create_conversation
from shared.models import (
    Instance, InstanceState,
    Message, MessageDirection, MessageStatus, MessageType,
    MessageFile, FileType,
)

__all__ = ("routes",)
routes = web.RouteTableDef()

MEDIA_ROOT: Final = Path(os.getenv("MEDIA_ROOT", "/app/media"))

_HANDLERS: dict[str, Callable[[dict[str, Any]], Awaitable[None]]] = {}


async def resolve_internal_id(api_id: int, session: AsyncSession) -> int | None:
    return await session.scalar(
        select(Instance.id).where(Instance.api_id == api_id)
    )


def handler(kind: str):
    def wrap(fn):
        _HANDLERS[kind] = fn
        return fn

    return wrap


# helpers

_STATUS2ENUM = {
    "sent": MessageStatus.sent,
    "delivered": MessageStatus.delivered,
    "read": MessageStatus.read,
    "failed": MessageStatus.error_api,
    "noAccount": MessageStatus.error_api,
    "notInGroup": MessageStatus.error_api,
}

_CALL_STATUS_HUMAN = {
    "offer":    "входящий звонок",
    "pickUp":   "принятый звонок",
    "hangUp":   "сброшенный звонок",
    "missed":   "пропущенный звонок (отменён звонившим)",
    "declined": "пропущенный звонок",
}


# webhook
@handler("incomingCall")
async def _handle_call(payload: dict[str, Any]) -> None:
    """
    Creates Call notification as incoming text message
    One rec on offer stage, then pickUp/missed/etc. as final status
    """
    inst_id = payload["instanceData"]["idInstance"]
    wa_id = payload["idMessage"]
    chat_id = payload["from"]
    status = payload["status"]  # offer / pickUp
    phone, gtype = chat_id.rsplit("@", 1)
    chat_name = phone
    if gtype.strip() == "g.us":
        chat_name += " (group)"

    human = _CALL_STATUS_HUMAN.get(status, status)

    async with async_session_maker() as db:
        inst_db_id = await resolve_internal_id(inst_id, db)
        if inst_db_id is None:
            logger.warning("Webhook for unknown instance %s – ignore", inst_id)
            return

        row = await db.scalar(
            select(Message)
            .where(Message.instance_id == inst_db_id,
                   Message.wa_message_id == wa_id)
        )

        conv = await get_or_create_conversation(db,
                                                instance_id=inst_db_id, chat_id=chat_id,
                                                phone=chat_id.split("@")[0], chat_name=chat_id.split("@")[0])

        if row is None:  # offer stage
            msg = Message(
                instance_id=inst_db_id,
                conversation_id=conv.id,
                wa_message_id=wa_id,
                chat_id=chat_id,
                chat_name=chat_name,
                from_app=False,
                direction=MessageDirection.inc,
                status=MessageStatus.incoming,
                message_type=MessageType.call,
                text=f"📞 {human}",
            )
            db.add(msg)

        else:          # final status
            row.text = f"📞 {human}"

        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()


@handler("outgoingMessageReceived")
@handler("outgoingAPIMessageReceived")
async def _handle_outgoing(payload: dict[str, Any]) -> None:
    inst_id = payload["instanceData"]["idInstance"]
    wa_id = payload["idMessage"]

    # early return
    async with async_session_maker() as db:
        inst_db_id = await resolve_internal_id(inst_id, db)
        if inst_db_id is None:
            logger.warning("Webhook for unknown instance %s – ignore", inst_id)
            return

        exists = await db.scalar(
            select(Message.id).where(
                Message.instance_id == inst_db_id,
                Message.wa_message_id == wa_id,
            )
        )
        if exists:
            logger.debug("Dup OUT msg %s – skip download/parse", wa_id)
            return  # skip the dupe

        # process payload as ORM obj, download media
        msg = await payload_to_msg(payload, db, im=app["client_manager"], db_instance_id=inst_db_id, incoming=False)
        if msg is None:  # _IGNORE_MTYPE
            return

        msg.direction = MessageDirection.out
        msg.from_app = False

        # attempt to save
        try:
            db.add(msg)
            await db.commit()
            logger.info("Saved OUT msg %s / %s", inst_id, wa_id)
        except IntegrityError:  # race condition: 2 webhooks at the same time (fixes high-demand instance errors)
            logger.debug("Race dup on %s – unique constraint won", wa_id)


@handler("outgoingMessageStatus")
async def _handle_status(payload: dict[str, Any]) -> None:
    """Статус ранее отправленного сообщения."""
    inst_id = payload["instanceData"]["idInstance"]
    wa_id = payload["idMessage"]
    new_st = _STATUS2ENUM.get(payload.get("status"))
    if not new_st:
        logger.debug("Unknown status %s; skip", payload.get("status"))
        return

    async with async_session_maker() as db:
        inst_db_id = await resolve_internal_id(inst_id, db)
        if inst_db_id is None:
            logger.warning("Webhook for unknown instance %s – ignore", inst_id)
            return

        msg: Message | None = await db.scalar(
            select(Message).where(
                Message.instance_id == inst_db_id,
                Message.wa_message_id == wa_id,
            )
        )
        if not msg:
            logger.debug("Status for unknown msg %s – ignore", wa_id)
            return
        if msg.status == new_st:
            return
        msg.status = new_st
        await db.commit()
        if new_st == MessageStatus.error_api:
            desc = payload.get("description") or payload["status"] or "неизвестная ошибка"
            await notify_send_error(db, msg, f"ошибка API ({desc})")
        logger.info("Msg %s → %s", wa_id, new_st.value)


@handler("stateInstanceChanged")
async def _handle_state(payload: dict[str, Any]) -> None:
    inst_id = payload["instanceData"]["idInstance"]
    new_state_raw = payload.get("stateInstance")
    #logger.info(f"New state notification: {json.dumps(payload)}")
    try:
        new_state = InstanceState(new_state_raw)
    except ValueError:
        logger.warning("Unknown instance state %s (id %s)", new_state_raw, inst_id)
        return

    async with async_session_maker() as db:
        inst = await db.scalar(select(Instance).options(selectinload(Instance.telegram_channel)).where(Instance.api_id == inst_id))
        if not inst:
            logger.error("Instance %s not found in DB", inst_id)
            return

        if inst.state != new_state:
            old = inst.state
            inst.state = new_state
            await db.commit()
            logger.info("Instance %s: %s → %s", inst_id, old.value, new_state.value)
            try:
                await bot.send_message(inst.telegram_channel.telegram_id,
                                       f"Статус инстанса {inst_id}: {old.value} → {new_state.value}")
            except Exception as e:
                logger.error("Telegram notify failed: %s", e)


@handler("incomingMessageReceived")
async def _handle_incoming(payload: dict[str, Any]) -> None:
    inst_id = payload["instanceData"]["idInstance"]
    async with async_session_maker() as db:
        inst_db_id = await resolve_internal_id(inst_id, db)
        if inst_db_id is None:
            logger.warning("Webhook for unknown instance %s – ignore", inst_id)
            return

        msg = await payload_to_msg(payload, db, im=app["client_manager"], db_instance_id=inst_db_id)
        if not msg:
            logger.info("Ignored msg %s of type: %s", msg.wa_message_id,
                        str(payload.get("messageData", {}).get("typeMessage", "unknown type")))
            return
        try:
            db.add(msg)
            await db.commit()
            logger.info("Saved msg %s / %s", msg.instance_id, msg.wa_message_id)
        except IntegrityError:
            await db.rollback()
            logger.warning("Dup webhook skipped (%s, %s)", msg.instance_id, msg.wa_message_id)


@routes.post("/green-api/webhook/")
async def green_webhook(req: web.Request) -> web.Response:
    payload = await req.json()
    #logger.info(json.dumps(payload))
    fn = _HANDLERS.get(payload.get("typeWebhook"))
    if fn:
        await fn(payload)
    else:
        logger.warning("Unhandled webhook %s", payload.get("typeWebhook"))
    return web.Response()
