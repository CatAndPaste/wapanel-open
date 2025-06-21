from __future__ import annotations

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from starlette.responses import Response, RedirectResponse

from admin.templating import templates
from admin.utils.db import get_session, async_session_maker
from admin.utils.files import _build_media_path, _detect_class, _public_url, notify_send_error, _save_one_message
from admin.utils.logger import logger
from admin.utils.security import require_admin, has_instance_access
from shared.crud.conversations import mark_all_messages_seen
from shared.crud.instance import get_instance_by_api_id
from shared.crud.message import (
    list_messages, get_message_by_id,
)
from shared.models import (
    Instance,
    Message,
    MessageDirection,
    MessageType,
    MessageStatus,
    User, FileType, MessageFile,
)

router = APIRouter(prefix="/chat")
PAGE = 20


# HELPERS
def _mk_chat_id(phone: str) -> str:
    """
    phone: «79951112233»  →  79951112233@c.us
    phone: «79951112233-g» → 79951112233@g.us
    """
    if phone.endswith("-g"):
        return f"{phone[:-2]}@g.us"
    return f"{phone}@c.us"


def _dir_class(msg: Message) -> str:
    # .inc to the left, .out/.sys to the right
    return "msg-left" if msg.direction is MessageDirection.inc else "msg-right"


@router.get("/{api_id}/new", response_class=HTMLResponse)
async def new_chat_form(
        api_id: int,
        request: Request,
        db: AsyncSession = Depends(get_session),
        user: User = Depends(require_admin),
):
    inst = await get_instance_by_api_id(db, api_id=api_id)
    if not inst or not has_instance_access(user, inst):
        raise HTTPException(404, "Инстанс не найден или нет доступа")

    return templates.TemplateResponse(
        "chat/new_chat.html",
        {"request": request, "inst": inst},
    )


@router.get("/{api_id}/{phone}", response_class=HTMLResponse)
async def chat_page(
        api_id: int,
        phone: str,
        request: Request,
        db: AsyncSession = Depends(get_session),
        user: User = Depends(require_admin),
):
    inst: Instance | None = await get_instance_by_api_id(db, api_id=api_id)
    if not inst or not has_instance_access(user, inst):
        raise HTTPException(404, "Инстанс не найден, или у вас нет к нему доступа")

    chat_id = _mk_chat_id(phone)

    messages: List[Message] = await list_messages(
        db,
        instance_id=inst.id,
        chat_id=chat_id,
        offset=0,
        limit=PAGE,
    )

    if not messages:
        raise HTTPException(404, "Чат не найден")

    await mark_all_messages_seen(db, instance_id=inst.id, chat_id=chat_id)

    return templates.TemplateResponse(
        "chat/chat.html",
        {
            "request": request,
            "instance": inst,
            "chat_id": chat_id,
            "phone": phone,
            "messages": messages,
            "_cls": _dir_class,
            "MessageDirection": MessageDirection,
            "offset": PAGE,
            "page_size": PAGE
        },
    )


# History realted
@router.get("/{api_id}/{phone}/history", response_class=HTMLResponse)
async def chat_history(
        api_id: int,
        phone: str,
        request: Request,
        offset: int = 0,
        session: AsyncSession = Depends(get_session),
        user: User = Depends(require_admin),
):
    inst: Instance | None = await get_instance_by_api_id(session, api_id=api_id)
    if not inst or not has_instance_access(user, inst):
        raise HTTPException(404)

    chat_id = _mk_chat_id(phone)

    msgs = await list_messages(
        session,
        instance_id=inst.id,
        chat_id=chat_id,
        offset=offset,
        limit=PAGE,
    )

    return templates.TemplateResponse(
        "chat/partials/message_list.html",
        {
            "request": request,
            "messages": msgs,
            "MessageDirection": MessageDirection,
            "_cls": _dir_class,
            "offset": offset + PAGE,
            "page_size": PAGE
        },
    )


@router.get("/{api_id}/{phone}/item/{msg_id}", response_class=HTMLResponse)
async def chat_item(api_id: int, phone: str, msg_id: int,
                    request: Request, db: AsyncSession = Depends(get_session),
                    user: User = Depends(require_admin)):
    inst = await get_instance_by_api_id(db, api_id=api_id)
    if not inst or not has_instance_access(user, inst):
        raise HTTPException(404)

    msg = await get_message_by_id(db, message_id=msg_id)
    if not msg or msg.instance_id != inst.id:
        raise HTTPException(404)

    return templates.TemplateResponse(
        "chat/partials/message_item.html",
        {
            "request": request,
            "m": msg,
            "_cls": _dir_class,
            "MessageDirection": MessageDirection,
        },
    )


# message sending
@router.post("/{api_id}/{phone}/send", status_code=204)
async def chat_send(
        api_id: int,
        phone: str,
        request: Request,
        msg: str = Form(""),
        files: list[UploadFile] = File(default_factory=list),
        db: AsyncSession = Depends(get_session),
        user: User = Depends(require_admin),
):
    inst = await get_instance_by_api_id(db, api_id=api_id)
    if not inst or not has_instance_access(user, inst):
        raise HTTPException(404)

    chat_id = _mk_chat_id(phone)
    clean = msg.replace("\r\n", "\n").replace("\r", "\n") or None
    if not clean and not files:
        raise HTTPException(422, "Текст сообщения пустой и файлы не выбраны")

    # message skeleton
    # 1. text, no files
    if clean and not files:
        await _save_one_message(db, inst, chat_id, text=clean, is_first=True)

    # 2. files
    for idx, up in enumerate(files):
        await _save_one_message(
            db, inst, chat_id,
            text=clean if idx == 0 else None,
            upload=up,
            is_first=(idx == 0),
        )

    await db.commit()


def _clean_phone(num: str) -> str:
    return num.strip().lstrip(" +\t")


@router.get("/{api_id}/new", response_class=HTMLResponse)
async def new_chat_form(
        api_id: int,
        request: Request,
        db: AsyncSession = Depends(get_session),
        user: User = Depends(require_admin),
):
    inst = await get_instance_by_api_id(db, api_id=api_id)
    if not inst or not has_instance_access(user, inst):
        raise HTTPException(404, "Инстанс не найден или нет доступа")

    return templates.TemplateResponse(
        "chat/new_chat.html",
        {"request": request, "inst": inst},
    )


@router.post("/{api_id}/new")
async def new_chat_submit(
        api_id: int,
        phone: str = Form(..., description="Номер телефона без +"),
        db: AsyncSession = Depends(get_session),
        user: User = Depends(require_admin),
):
    inst = await get_instance_by_api_id(db, api_id=api_id)
    if not inst or not has_instance_access(user, inst):
        raise HTTPException(404, "Инстанс не найден или нет доступа")

    phone = _clean_phone(phone)
    if not phone:
        raise HTTPException(422, "Номер не указан")

    chat_id = _mk_chat_id(phone)

    # check if exists
    exists_msg = await db.scalar(
        select(func.count()).select_from(Message).where(
            Message.instance_id == inst.id,
            Message.chat_id == chat_id,
        )
    )

    if not exists_msg:
        sys_msg = Message(
            instance_id=inst.id,
            chat_id=chat_id,
            chat_name=phone,
            wa_message_id=None,
            from_app=True,
            direction=MessageDirection.sys,
            status=MessageStatus.incoming,
            message_type=MessageType.notification,
            is_archived=True,
            text=f"Диалог с {phone} создан",
        )
        db.add(sys_msg)
        try:
            await db.commit()
        except IntegrityError:  # JUST IN CASE!
            await db.rollback()

    return RedirectResponse(
        url=f"/chat/{api_id}/{phone}",
        status_code=303,
    )
