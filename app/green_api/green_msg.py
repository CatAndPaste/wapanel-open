from __future__ import annotations

from urllib.parse import unquote, urlparse
import asyncio
import os
from pathlib import Path
from datetime import datetime
from random import random
from typing import Any, Final

from aiohttp import ClientSession, ClientTimeout, ClientError
from sqlalchemy.ext.asyncio import AsyncSession

from app.loader import logger
from shared.crud.conversations import get_or_create_conversation
from shared.models import (
    Message, MessageDirection, MessageStatus, MessageType,
    MessageFile, FileType,
)

MEDIA_ROOT: Final = Path(os.getenv("MEDIA_ROOT", "/app/media"))

_EXT2FCLS: dict[str, tuple[MessageType, FileType]] = {
    "imageMessage": (MessageType.file_image, FileType.image),
    "stickerMessage": (MessageType.file_image, FileType.image),
    "videoMessage": (MessageType.file_video, FileType.video),
    "audioMessage": (MessageType.file_audio, FileType.audio),
    "documentMessage": (MessageType.file_doc, FileType.other),
}

_IGNORE_MTYPE: set[str] = {
    "pollUpdateMessage",
    "interactiveButtonsReply",
    "templateButtonsReplyMessage",
}


# HELPERS
async def download_safe(
    url: str,
    fname: str,
    session: ClientSession,
    *,
    retries: int = 3,
    backoff: float = 1.5,
    chunk: int = 1 << 14,
) -> tuple[str, int] | None:
    """
    Attempts to download *url* → MEDIA_ROOT/<yyyy>/<mm>/<fname>.
    success -> (abs_path, size)
    error -> None (+ reason in logger.error/exception)
    """
    """Downloads *url* into media/<yyyy>/<mm>/<fname>. Returns (abs_path, size)."""
    if not url or url.strip() == "":
        return None

    dst_dir = MEDIA_ROOT / datetime.utcnow().strftime("%Y/%m")
    dst_dir.mkdir(parents=True, exist_ok=True)
    local = dst_dir / fname
    attempt = 0
    while attempt <= retries:
        try:
            size = 0
            timeout = ClientTimeout(total=70, sock_read=60, sock_connect=10)

            async with session.get(url, timeout=timeout) as resp:
                resp.raise_for_status()
                with local.open("wb") as fh:
                    async for chunk_data in resp.content.iter_chunked(chunk):
                        size += len(chunk_data)
                        fh.write(chunk_data)

            if size == 0:
                raise ValueError("downloaded file is empty")

            return str(local), size

        except (ClientError, asyncio.TimeoutError, OSError, ValueError) as e:
            attempt += 1
            logger.warning("Download failed (try %s/%s) url=%s err=%s", attempt, retries, url, e)
            try:
                local.unlink(missing_ok=True)
            except OSError:
                pass

            if attempt > retries:
                logger.error("All retries exhausted; give up downloading %s", url)
                return None
            await asyncio.sleep((backoff ** attempt) * (0.5 + random() / 2))


def _public_url(abs_path: str) -> str:
    return "/media/" + Path(abs_path).relative_to(MEDIA_ROOT).as_posix()


def _location_to_text(data: dict) -> str:
    d = data["locationMessageData"]
    return (f"<местоположение>\n"
            f"{d.get('nameLocation') or ''}\n"
            f"{d.get('address', '')}\n"
            f"шир. {d['latitude'] or "--"}, долг. {d['longitude' or "--"]}")


def _contact_to_text(d: dict) -> str:
    name = d.get("displayName") or "Contact"
    phones = ", ".join(
        line.split("waid=")[-1] for line in d["vcard"].splitlines() if "waid=" in line
    )
    return f"<контакт>\n{name}:\n{phones}"


# ! ENTRY POINT
async def payload_to_msg(
    payload: dict[str, Any],
    session: AsyncSession,
    *,
    db_instance_id: int,
    im,                       # ClientManager (no type, so no dependencies here, do not fix in the future)
    incoming: bool = True,
    archived: bool = False
) -> Message | None:
    """
    Processes GreenAPI payload into ORM Message obj
    """
    mdata = payload["messageData"]
    mtype = mdata["typeMessage"]

    if mtype in _IGNORE_MTYPE:
        return None

    # general
    chat_id = payload["senderData"]["chatId"]
    phone, gtype = chat_id.rsplit("@", 1)
    chat_name = payload["senderData"].get("senderName") or phone
    #if gtype.strip() == "g.us":
    #    chat_name += " (group)"

    conv = await get_or_create_conversation(session,
                                            instance_id=db_instance_id, chat_id=chat_id,
                                            phone=phone, chat_name=chat_name)

    msg = Message(
        instance_id=db_instance_id,
        conversation_id=conv.id,
        wa_message_id=payload["idMessage"],
        chat_id=chat_id,
        chat_name=chat_name,
        created_at=datetime.utcfromtimestamp(payload["timestamp"]),
        from_app=False,
        direction=MessageDirection.inc if incoming else MessageDirection.out,
        status=MessageStatus.incoming,
        message_type=MessageType.notification,
        text=None,
        is_archived=archived,
    )

    # message types
    # a. text
    if mtype in {"textMessage", "extendedTextMessage", "reactionMessage", "quotedMessage"}:
        msg.message_type = MessageType.text
        field = "textMessageData" if mtype == "textMessage" else "extendedTextMessageData"
        msg.text = mdata[field]["textMessage" if mtype == "textMessage" else "text"]

    # b. media
    elif mtype in _EXT2FCLS:
        msg.message_type, fcls = _EXT2FCLS[mtype]
        fm = mdata["fileMessageData"]
        fname = fm.get("fileName")
        if not fname:
            path = unquote(urlparse(fm.get("downloadUrl", "")).path)
            fname = os.path.basename(path) or f"{msg.wa_message_id}"
        if '.' not in fname and fm.get("mimeType"):
            ext = fm["mimeType"].split('/')[-1]
            fname = f"{fname}.{ext}"
        fcaption = fm.get("caption")
        dl_url = fm.get("downloadUrl")  # Green API download URL

        async with im.get_client(payload["instanceData"]["idInstance"]) as cl:
            if not dl_url:
                dl_url = await cl.download_file(
                    chat_id=msg.chat_id,
                    id_message=msg.wa_message_id
                ) or ""

            downloaded = await download_safe(dl_url, fname, cl.session)

        if downloaded is None:
            msg.message_type = MessageType.text
            if fcaption:
                msg.text = f"{fcaption}\n<Не удалось скачать файл ({fm.get('fileName') or '--'})>"
            else:
                msg.text = f"<Не удалось скачать файл ({fm.get('fileName') or 'no-name'})>"
        else:
            fpath, fsize = downloaded
            file_rec = MessageFile(
                file_type=fcls,
                name=fname,
                mime=fm["mimeType"],
                file_path=fpath,
                file_url=_public_url(fpath),
                size=fsize,
            )
            msg.files.append(file_rec)
            msg.text = fm.get("caption")

    # c. special messages -> plain text
    elif mtype == "locationMessage":
        msg.message_type = MessageType.text
        msg.text = _location_to_text(mdata)

    elif mtype == "contactMessage":
        msg.message_type = MessageType.text
        msg.text = _contact_to_text(mdata["contactMessageData"])

    elif mtype == "contactsArrayMessage":
        msg.message_type = MessageType.text
        arr = mdata["messageData"]["contacts"]
        msg.text = "\n".join(_contact_to_text(c) for c in arr)

    elif mtype == "groupInviteMessage":
        g = mdata["groupInviteMessageData"]
        msg.message_type = MessageType.text
        msg.text = f"<приглашение в группу>\n{g['groupName']} ({g['groupJid']})"

    elif mtype == "pollMessage":
        p = mdata["pollMessageData"]
        opts = "\n".join(f"- {o['optionName']}" for o in p["options"])
        msg.message_type = MessageType.text
        msg.text = f"<опрос>\n{p['name']}\n{opts}"

    elif mtype == "interactiveButtons":
        b = mdata["interactiveButtons"]
        btns = " | ".join(btn["buttonText"] for btn in b["buttons"])
        msg.message_type = MessageType.text
        msg.text = f"{b.get('titleText', '')}\n{b['contentText']}\n-------\n{btns}"

    else:
        # for debug purposes only
        msg.message_type = MessageType.text
        msg.text = f"<Сообщение непредусмотренного типа: {mtype}>"

    return msg