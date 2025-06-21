from __future__ import annotations
import asyncio, math, json, os
from datetime import datetime
from typing import Any

from aiogram import Bot
from sqlalchemy import select
from typing import Any, TypedDict, Literal

from sqlalchemy.orm import selectinload

from app.green_api.green_msg import payload_to_msg
from app.green_api.manager import ClientManager
from app.utils.db import async_session_maker
from app.loader import logger, bot
from shared.models import Instance, Message

history_lock: dict[int, asyncio.Lock] = {}
_DOWNLOAD_LIMIT = 10000     # 10K is the maximum number of messages WhatsApp gives to GreenAPI according to docs


class SenderData(TypedDict, total=False):
    chatId: str
    sender: str
    chatName: str
    senderName: str
    senderContactName: str


class PayloadDict(TypedDict, total=False):
    typeWebhook: str
    idMessage: str
    timestamp: int
    status: str
    instanceData: dict[str, Any]
    senderData: SenderData
    messageData: dict[str, Any]


_OUTGOING_BY_API = "outgoingAPIMessageReceived"
_OUTGOING_BY_PHONE = "outgoingMessageReceived"
_INCOMING = "incomingMessageReceived"

_FILE_MTYPES = {
    "imageMessage",
    "videoMessage",
    "audioMessage",
    "documentMessage",
    "stickerMessage",
}


def _mk_sender(entry: dict[str, Any]) -> SenderData:
    """senderData"""
    return {
        "chatId": entry["chatId"],
        "sender": entry.get("senderId", entry["chatId"]),
        "chatName": entry.get("senderName", ""),
        "senderName": entry.get("senderName", ""),
        "senderContactName": entry.get("senderContactName", ""),
    }


def _file_section(entry: dict[str, Any]) -> dict[str, Any]:
    """fileMessageData"""
    return {
        "downloadUrl": entry.get("downloadUrl"),
        "caption": entry.get("caption") or "",
        "fileName": entry.get("fileName") or "",
        "jpegThumbnail": entry.get("jpegThumbnail") or "",
        "mimeType": entry.get("mimeType") or "",
        "isAnimated": entry.get("isAnimated", False),
    }


def _split_message(msg: str, *, with_photo: bool) -> list[str]:
    """Split the text into parts considering Telegram limits."""
    parts = []
    while msg:
        # Determine the maximum message length based on
        # with_photo and whether it's the first iteration
        # (photo is sent only with the first message).
        if parts:
            max_msg_length = 4096
        elif with_photo:
            max_msg_length = 1024
        else:
            max_msg_length = 4096

        if len(msg) <= max_msg_length:
            # The message length fits within the maximum allowed.
            parts.append(msg)
            break

        # Cut a part of the message with the maximum length from msg
        # and find a position for a break by a newline character.
        part = msg[:max_msg_length]
        first_ln = part.rfind("\n")

        if first_ln != -1:
            # Newline character found.
            # Split the message by it, excluding the character itself.
            new_part = part[:first_ln]
            parts.append(new_part)

            # Trim msg to the length of the new part
            # and remove the newline character.
            msg = msg[first_ln + 1:]
            continue

        # No newline character found in the message part.
        # Try to find at least a space for a break.
        first_space = part.rfind(" ")

        if first_space != -1:
            # Space character found.
            # Split the message by it, excluding the space itself.
            new_part = part[:first_space]
            parts.append(new_part)

            # Trimming msg to the length of the new part
            # and removing the space.
            msg = msg[first_space + 1:]
            continue

        # No suitable place for a break found in the message part.
        # Add the current part and trim the message to its length.
        parts.append(part)
        msg = msg[max_msg_length:]

    return parts


async def _notify(app, api_id: int, text: str) -> None:
    """
    Attempt to send the message in Telegram channel assigned to instance,
    best-effort
    """
    try:
        bot_inst: Bot | None = bot
        if not bot_inst:
            return

        async with async_session_maker() as db:
            inst: Instance | None = await db.scalar(
                select(Instance)
                .options(selectinload(Instance.telegram_channel))
                .where(Instance.api_id == api_id)
            )

            tg = inst and inst.telegram_channel
            if not (tg and tg.is_active):
                return

            parts = _split_message(text, with_photo=False)
            for part in parts:
                await bot_inst.send_message(tg.telegram_id, part)
    except Exception as e:
        logger.warning("TG-notify failed: %s", e)


def history_entry_to_payload(entry: dict[str, Any], api_id: int) -> PayloadDict:
    """
    getChatHistory -> dict
    """
    # typeWebhook
    if entry["type"] == "incoming":
        type_webhook = _INCOMING
    else:  # outgoing
        type_webhook = (
            _OUTGOING_BY_API if entry.get("sendByApi") else _OUTGOING_BY_PHONE
        )

    payload: PayloadDict = {
        "typeWebhook": type_webhook,
        "idMessage": entry["idMessage"],
        "timestamp": entry["timestamp"],
        "instanceData": {"idInstance": api_id},
        "senderData": _mk_sender(entry),
        "messageData": {},
    }

    mtype: str = entry["typeMessage"]
    mdata: dict[str, Any] = {"typeMessage": mtype}
    payload["messageData"] = mdata

    if mtype == "textMessage":
        mdata["textMessageData"] = {"textMessage": entry["textMessage"]}

    elif mtype == "extendedTextMessage":
        txt = entry.get("textMessage") or entry["extendedTextMessage"]["text"]
        mdata["extendedTextMessageData"] = {"text": txt}

    elif mtype == "reactionMessage":
        # extendedTextMessageData
        mdata["extendedTextMessageData"] = entry["extendedTextMessageData"]
        if "quotedMessage" in entry:
            mdata["quotedMessage"] = entry["quotedMessage"]

    elif mtype == "quotedMessage":
        mdata["extendedTextMessageData"] = entry["extendedTextMessage"]
        mdata["quotedMessage"] = entry["quotedMessage"]

    elif mtype in _FILE_MTYPES:
        mdata["fileMessageData"] = _file_section(entry)

    elif mtype == "locationMessage":
        mdata["locationMessageData"] = entry["location"]

    elif mtype == "contactMessage":
        mdata["contactMessageData"] = entry["contact"]

    elif mtype == "contactsArrayMessage":
        mdata["messageData"] = {"contacts": entry["contacts"]}

    elif mtype in {"pollMessage", "pollUpdateMessage"}:
        mdata["pollMessageData"] = entry["pollMessageData"]

    elif mtype == "interactiveButtons":
        mdata["interactiveButtons"] = entry["interactiveButtons"]

    if "statusMessage" in entry:
        payload["status"] = entry["statusMessage"]

    return payload


# main routine
async def load_history(app, api_id: int, *, wait_authorized: bool = False) -> None:
    cm: ClientManager = app["client_manager"]
    lock = history_lock.setdefault(api_id, asyncio.Lock())

    await _notify(app, api_id, f"Запущена задача загрузки истории сообщений для инстанса {api_id}...")
    logger.info("Download history for %s started (wait_auth=%s)", api_id, wait_authorized)

    async with cm.get_client(api_id) as client:
        if wait_authorized:
            for _ in range(30):
                if (await client.get_state()) == "authorized":
                    break
                await asyncio.sleep(2)
            else:
                await _notify(app, api_id, f"Инстанс {api_id} не авторизован, не удалось загрузить историю "
                                           f"сообщений. Пожалуйста войдите в инстанс и повторите попытку.")
                raise RuntimeError("instance not authorized")
        else:
            if (await client.get_state()) != "authorized":
                await _notify(app, api_id, f"Инстанс {api_id} не авторизован, не удалось загрузить историю "
                                           f"сообщений. Пожалуйста войдите в инстанс и повторите попытку.")
                raise RuntimeError("instance not authorized")

        # last messages for past 10 years
        minutes = 365 * 24 * 60 * 10    # ~5mils
        inc = await client.last_incoming(minutes=minutes)
        out = await client.last_outgoing(minutes=minutes)
        chat_ids = {m["chatId"] for m in (*inc, *out)}
        await _notify(app, api_id, f"Для инстанса {api_id} получено {len(inc)} входящих и {len(out)} исходящих "
                                   f"сообщений, сохраняю...")
        logger.info("Instance %s: lastIncoming=%s, lastOutgoing=%s", api_id, len(inc), len(out))

    # internal inst id
    async with async_session_maker() as db:
        inst_db_id = await db.scalar(select(Instance.id).where(Instance.api_id == api_id))
        if inst_db_id is None:
            await _notify(app, api_id, f"Что-то пошло не так, инстанс {api_id} больше не найден в БД...")
            logger.error("Instance %s vanished from DB", api_id)
            return

    info = ""
    # getChatHistory per chat
    async with cm.get_client(api_id) as client:
        for chat_id in chat_ids:
            try:
                total_saved = total_skipped = 0
                history = await client.get_chat_history(chat_id=chat_id, count=_DOWNLOAD_LIMIT)
                logger.info("Chat %s: history items=%s", chat_id, len(history))

                async with async_session_maker() as db:
                    for entry in history:
                            wa_id = entry["idMessage"]
                            exists = await db.scalar(
                                select(Message.id).where(
                                    Message.instance_id == inst_db_id,
                                    Message.wa_message_id == wa_id,
                                ).limit(1)
                            )
                            if exists:
                                total_skipped += 1
                                continue

                            payload = history_entry_to_payload(entry, api_id)
                            msg = await payload_to_msg(payload,
                                                       db,
                                                       db_instance_id=inst_db_id,
                                                       im=cm,
                                                       incoming=entry["type"] == "incoming",
                                                       archived=True)
                            if msg is None:
                                total_skipped += 1
                                continue
                            msg.is_archived = True
                            db.add(msg)
                            total_saved += 1
                    await db.commit()
                info += (f"Чат {chat_id} (https://wapanel.ru/chat/{api_id}/{chat_id.rsplit("@", 1)[0]}, {len(history)} "
                         f"сообщений): сохранено: {total_saved}, пропущено: {total_skipped}\n")
                logger.info("Download history for %s finished: saved=%s, skipped=%s", chat_id, total_saved,
                            total_skipped)
            except Exception as e:
                info += f"Чат {chat_id}: не удалось загрузить историю чата\n"
                logger.error(f"Download history for {chat_id} failed: {e}")
    await _notify(app, api_id, f"Загрузка сообщений для инстанса {api_id} завершена\n{info}")
