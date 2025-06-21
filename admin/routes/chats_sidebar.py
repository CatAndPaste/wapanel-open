# routes/chats_sidebar.py
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct
from starlette.requests import Request

from admin.utils.db import get_session
from admin.utils.security import require_admin, has_instance_access
from shared.models import (
    Instance, Conversation, Message, MessageDirection
)
from shared.crud.conversations import list_conversations, fetch_dialogs
from admin.templating import templates

router = APIRouter(prefix="/chats/sidebar")


# left: instances
@router.get("/instances", response_class=HTMLResponse)
async def sidebar_instances(
    request: Request,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_admin),
):
    # available
    allowed = (
        None if (user.full_access or user.is_owner)
        else {inst.id for inst in user.instances}
    )

    # per-inst unread
    m, c = Message.__table__, Conversation.__table__
    unread_per_inst = (
        select(
            c.c.instance_id,
            func.count(distinct(m.c.id)).label("unread")
        )
        .join(m, m.c.conversation_id == c.c.id)
        .where(
            m.c.direction == MessageDirection.inc,
            m.c.is_seen.is_(False)
        )
        .group_by(c.c.instance_id)
        .cte("u")
    )

    stmt = (
        select(Instance, func.coalesce(unread_per_inst.c.unread, 0).label("unread"))
        .join(unread_per_inst, unread_per_inst.c.instance_id == Instance.id, isouter=True)
    )
    if allowed is not None:
        stmt = stmt.where(Instance.id.in_(allowed))

    res = await session.execute(stmt)
    items = res.all()           # [(Instance, unread), …]

    return templates.TemplateResponse(
        "chats/partials/sidebar_instances.html",
        {
            "request": request,
            "inst_items": items
        }
    )


# middle: dialogues
@router.get("/{api_id}/dialogs", response_class=HTMLResponse)
async def sidebar_dialogs(
    request: Request,
    api_id: int,
    session: AsyncSession = Depends(get_session),
    user=Depends(require_admin),
):
    inst: Instance | None = await session.scalar(
        select(Instance).where(Instance.api_id == api_id)
    )
    if not inst or not has_instance_access(user, inst):
        raise HTTPException(404)

    dialogs = await fetch_dialogs(session, instance_id=inst.id, limit=200)

    return templates.TemplateResponse(
        "chats/partials/sidebar_dialogs.html",
        {
            "request": request,
            "inst": inst,
            "dialogs": dialogs,
        }
    )
