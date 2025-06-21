from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, case, distinct
from starlette.responses import Response, RedirectResponse

from admin.templating import templates
from admin.utils.db import get_session, async_session_maker
from admin.utils.files import _build_media_path, _detect_class, _public_url, notify_send_error, _save_one_message
from admin.utils.logger import logger
from admin.utils.security import require_admin, has_instance_access
from shared.crud.instance import get_instance_by_api_id
from shared.models import (
    Instance,
    Message,
    MessageDirection,
    MessageType,
    MessageStatus,
    User, FileType, MessageFile, Conversation, conversation_tags,
)

router = APIRouter(prefix="/chats")


@router.get("/", response_class=HTMLResponse)
async def new_chat_form(
        request: Request,
        db: AsyncSession = Depends(get_session),
        user: User = Depends(require_admin),
):
    return templates.TemplateResponse(
        "chats/layout.html",
        {"request": request},
    )



class ChatSummary(BaseModel):
    id: int
    instance_api_id: int
    chat_id: str
    title: Optional[str] = None
    phone: Optional[str] = None

    last_message_at: Optional[datetime] = None
    last_message: Optional[str] = None

    unread: int

    class Config:
        orm_mode = True


def _prefixed_text():
    return (
        case(
            (Message.direction == MessageDirection.out, "Вы: "),
            (Message.direction == MessageDirection.sys, "INFO: "),
            else_="",
        ).concat(Message.text)
    )


@router.get("/{api_id}/list", response_model=List[ChatSummary])
async def list_chats_for_instance(
    api_id: int,
    *,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
    tag_ids: List[int] = Query([], alias="tag"),
    q: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = 0,
):
    # access
    inst_row = await session.execute(
        select(Instance).where(Instance.api_id == api_id)
    )
    inst: Instance | None = inst_row.scalar_one_or_none()
    if not inst or not has_instance_access(user, inst):
        raise HTTPException(status_code=404, detail="Инстанс не найден или нет доступа")

    # 2) allowed_ids = {inst.id} (EXACTLY 1)
    allowed_ids = {inst.id}

    # 3) CTE
    last_msg = (
        select(
            Message.conversation_id,
            Message.text,
            Message.direction,
            Message.created_at.label("msg_at")
        )
        .order_by(Message.conversation_id, Message.created_at.desc())
        .distinct(Message.conversation_id)
        .cte("last_msg")
    )

    c, i, m = Conversation.__table__, Instance.__table__, Message.__table__

    base = (
        select(
            c.c.id,
            i.c.api_id.label("instance_api_id"),
            c.c.chat_id,
            c.c.title,
            c.c.phone,
            last_msg.c.msg_at.label("last_message_at"),
            case(
                (last_msg.c.direction == MessageDirection.out, "Вы: "),
                (last_msg.c.direction == MessageDirection.sys, "INFO: "),
                else_=""
            ).concat(last_msg.c.text).label("last_message"),
            func.count(distinct(m.c.id)).filter(
                (m.c.direction == MessageDirection.inc) &
                (m.c.is_seen.is_(False))
            ).label("unread"),
        )
        .join(i, i.c.id == c.c.instance_id)
        .join(last_msg, last_msg.c.conversation_id == c.c.id, isouter=True)
        .join(m, m.c.conversation_id == c.c.id)
        .where(i.c.id.in_(allowed_ids))
        .group_by(
            c.c.id, i.c.api_id,
            c.c.chat_id, c.c.title, c.c.phone,
            last_msg.c.msg_at, last_msg.c.text, last_msg.c.direction
        )
    )

    if tag_ids:
        base = base.join(conversation_tags).where(conversation_tags.c.tag_id.in_(tag_ids))
    if q:
        like = f"%{q}%"
        base = base.where((c.c.title.ilike(like)) | (c.c.phone.ilike(like)))

    stmt = (
        base.order_by(c.c.pinned.desc(), last_msg.c.msg_at.desc().nullslast())
            .limit(limit).offset(offset)
    )

    rows = (await session.execute(stmt)).mappings().all()
    return [ChatSummary(**r) for r in rows]


class InstanceSummary(BaseModel):
    api_id: int
    name: str | None = None
    unread_total: int

    class Config:
        orm_mode = True


@router.get("/list", response_model=List[InstanceSummary])
async def list_instances_with_unread(
    *,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin)
):
    # available instances
    if user.full_access or user.is_owner:
        allowed_ids: set[int] | None = None
    else:
        allowed_ids = {inst.id for inst in user.instances}
        if not allowed_ids:
            return []

    # 2) agr unread for instance_id
    m = Message.__table__
    c = Conversation.__table__
    i = Instance.__table__

    unread_sub = (
        select(
            c.c.instance_id.label("inst_id"),
            func.count(distinct(m.c.id)).label("unread")
        )
        .join(m, m.c.conversation_id == c.c.id)
        .where(
            m.c.direction == MessageDirection.inc,
            m.c.is_seen.is_(False)
        )
        .group_by(c.c.instance_id)
        .cte("unread")
    )

    stmt = (
        select(
            i.c.api_id,
            i.c.name,
            func.coalesce(unread_sub.c.unread, 0).label("unread_total")
        )
        .join(unread_sub, unread_sub.c.inst_id == i.c.id, isouter=True)
    )
    if allowed_ids is not None:
        stmt = stmt.where(i.c.id.in_(allowed_ids))

    stmt = stmt.order_by(i.c.name.nullslast(), i.c.api_id)

    rows = (await session.execute(stmt)).mappings().all()
    return [InstanceSummary(**r) for r in rows]
