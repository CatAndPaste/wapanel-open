from __future__ import annotations

import asyncio, mimetypes, tempfile
import os
from datetime import datetime
from pathlib import Path
from typing import Final

from aiogram import F, Router
from aiogram.types import (Message, File, Sticker, Voice)
from aiogram.types.reaction_type_emoji import ReactionTypeEmoji

from app.green_api.green_msg import _public_url
from app.loader import bot, logger
from app.utils.db import async_session_maker
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from shared.crud.conversations import get_or_create_conversation
from shared.models import (
    Message as MsgDB,
    MessageFile, FileType,
    MessageDirection, MessageType, MessageStatus,
)

router = Router(name="relay_to_whatsapp")

MEDIA_ROOT: Final = Path(os.getenv("MEDIA_ROOT", "/app/media"))


# helpers
def _detect_class(mime: str) -> FileType:
    if mime.startswith("image/"):
        return FileType.image
    if mime.startswith("video/"):
        return FileType.video
    if mime.startswith("audio/"):
        return FileType.audio
    return FileType.other


TMP_DIR = Path(tempfile.gettempdir()) / "tg_uploads"
TMP_DIR.mkdir(exist_ok=True)


def _build_media_path(fname: str) -> Path:
    """
    Generates path /app/media/2025/06/<fname> or /app/media/2025/06/<fname>_N if file already exists
    """
    dst_dir = MEDIA_ROOT / datetime.utcnow().strftime("%Y/%m")
    dst_dir.mkdir(parents=True, exist_ok=True)

    stem, suffix = Path(fname).stem, Path(fname).suffix
    candidate = dst_dir / fname
    n = 1
    while candidate.exists():
        candidate = dst_dir / f"{stem}_{n}{suffix}"
        n += 1
    return candidate


async def _download_tg_file(file_id: str) -> tuple[str, str, str]:
    """
    downloads TG file and saves into media/YY/MM,
    returns (absolute_path, stored_name, mime)
    """
    tg_file: File = await bot.get_file(file_id)

    # orig name if exists
    orig_name = Path(tg_file.file_path).name or f"{file_id}"
    local_path = _build_media_path(orig_name)

    # prepare dirs
    local_path.parent.mkdir(parents=True, exist_ok=True)

    await bot.download_file(tg_file.file_path, destination=local_path)

    mime, _ = mimetypes.guess_type(local_path.name)
    return str(local_path), local_path.name, mime or "application/octet-stream"


async def _react(msg: Message, ok: bool) -> None:
    emoji = "👍" if ok else "😡"
    try:
        await bot.set_message_reaction(
            chat_id=msg.chat.id,
            message_id=msg.message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception:
        await msg.reply(emoji)


@router.channel_post(F.reply_to_message)
async def relay_channel_reply(msg: Message) -> None:
    """
    Reply -> Message(direction=out, status=pending), quick response
    """

    # find Message in DB, reply was sent to
    async with (async_session_maker() as db):
        parent: MsgDB | None = await db.scalar(
            select(MsgDB)
            .where(MsgDB.tg_message_id == msg.reply_to_message.message_id)
            .options(selectinload(MsgDB.instance), selectinload(MsgDB.conversation))
        )
        if not parent or parent.direction is not MessageDirection.inc:
            return

        try:
            inst = parent.instance
            conv = parent.conversation

            out_msg = MsgDB(
                instance_id=inst.id,
                conversation_id=conv.id,
                chat_id=parent.chat_id,
                direction=MessageDirection.out,
                tg_message_id=msg.message_id,
                chat_name=parent.chat_id.split("@")[0],
                from_app=True,
                status=MessageStatus.pending,
                message_type=MessageType.text,
                text=msg.text or msg.caption or "",
                quote_id=parent.id,
            )

            tg_file = None
            # attachments
            if msg.photo or msg.video or msg.audio or msg.voice or msg.document:
                # correct File type
                if msg.photo:
                    tg_file = max(msg.photo, key=lambda p: p.file_size or 0)
                elif msg.video:
                    tg_file = msg.video
                elif msg.audio:
                    tg_file = msg.audio
                elif msg.voice:
                    tg_file = msg.voice
                    fcls = FileType.audio
                else:
                    tg_file = msg.document
            elif msg.sticker:
                st: Sticker = msg.sticker
                if st.is_animated or st.is_video:
                    await _react(msg, False)
                    return
                tg_file = st

            if tg_file:
                local_path, fname, mime = await _download_tg_file(tg_file.file_id)

                if isinstance(tg_file, Sticker) and mime == "application/octet-stream":
                    mime = "image/webp"

                if isinstance(tg_file, Voice):
                    fname = f"{tg_file.file_id}.ogg"

                fclass = _detect_class(mime)

                kind_map = {
                    FileType.image: MessageType.file_image,
                    FileType.video: MessageType.file_video,
                    FileType.audio: MessageType.file_audio,
                    FileType.other: MessageType.file_doc,
                }
                out_msg.message_type = kind_map[fclass]

                out_msg.files.append(
                    MessageFile(
                        file_type=fclass,
                        name=fname,
                        mime=mime,
                        file_path=local_path,
                        file_url=_public_url(local_path),
                    )
                )
            try:
                db.add(out_msg)
                await db.commit()
            except Exception as e:
                logger.error(f"Something went wrong while saving TG message: {e}")
                await _react(msg, False)
        except Exception as e:
            logger.error(f"Something went wrong while saving TG message: {e}")
            await _react(msg, False)
