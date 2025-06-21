from pathlib import Path
from datetime import datetime
import mimetypes, os
from typing import Final

import aiofiles
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from admin.utils.logger import logger
from shared.crud.conversations import get_or_create_conversation
from shared.models import FileType, Message, MessageDirection, MessageStatus, MessageType, MessageFile, Instance
import shared.locale as L

MEDIA_ROOT: Final = Path(os.getenv("MEDIA_ROOT", "/app/media"))


async def notify_send_error(db, orig: Message, reason: str) -> None:
    """
    Creates system notification Message in DB with error-text.
    """
    # TODO: ANOTHER PLACES TO SAVE MSG TO DB
    sys_msg = Message(
        instance_id=orig.instance_id,
        conversation_id=orig.conversation_id,
        chat_id=orig.chat_id,
        chat_name=orig.chat_name,
        wa_message_id=None,
        direction=MessageDirection.sys,
        from_app=True,
        status=MessageStatus.incoming,
        message_type=MessageType.notification,
        text=f"{L.ERR_PREFIX}{reason}",
    )
    db.add(sys_msg)
    await db.commit()


async def _save_one_message(
        db: AsyncSession,
        inst: Instance,
        chat_id: str,
        *,
        text: str | None = None,
        upload: UploadFile | None = None,
        is_first: bool = False
) -> None:
    msg_type = MessageType.text
    new_file: MessageFile | None = None

    if upload:
        dst = _build_media_path(upload.filename or "file")
        async with aiofiles.open(dst, "wb") as f:
            while chunk := await upload.read(1024 * 1024):
                await f.write(chunk)

        mime = upload.content_type or "application/octet-stream"
        f_cls = _detect_class(mime)

        msg_type = {
            FileType.image: MessageType.file_image,
            FileType.video: MessageType.file_video,
            FileType.audio: MessageType.file_audio,
            FileType.other: MessageType.file_doc,
        }[f_cls]

        new_file = MessageFile(
            file_type=f_cls,
            name=dst.name,
            mime=mime,
            file_path=str(dst),
            file_url=_public_url(dst),
            size=dst.stat().st_size,
        )

    conv = await get_or_create_conversation(db,
                                            instance_id=inst.id, chat_id=chat_id,
                                            phone=chat_id.split("@")[0], chat_name=chat_id.split("@")[0])

    db_msg = Message(
        instance_id=inst.id,
        conversation_id=conv.id,
        chat_id=chat_id,
        chat_name=chat_id.split("@")[0],
        from_app=True,
        direction=MessageDirection.out,
        status=MessageStatus.pending,
        message_type=msg_type,
        text=text if is_first else None,
    )

    if new_file:
        db_msg.files.append(new_file)

    db.add(db_msg)
    await db.flush()


def _build_media_path(fname: str) -> Path:
    dst = MEDIA_ROOT / datetime.utcnow().strftime("%Y/%m")
    dst.mkdir(parents=True, exist_ok=True)
    stem, suf = Path(fname).stem, Path(fname).suffix
    candidate = dst / fname
    n = 1
    while candidate.exists():
        candidate = dst / f"{stem}_{n}{suf}";
        n += 1
    return candidate


def _public_url(p: Path | str) -> str:
    return "/media/" + str(Path(p).relative_to(MEDIA_ROOT)).replace("\\", "/")


def _detect_class(mime: str) -> FileType:
    if mime.startswith("image/"): return FileType.image
    if mime.startswith("video/"): return FileType.video
    if mime.startswith("audio/"): return FileType.audio
    return FileType.other
