from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from starlette import status

from admin.utils.logger import logger
from admin.websockets.manager import WSManager, ChatWSManager
from admin.utils.security import require_admin, can_manage_users, has_instance_access
from shared.crud.instance import list_instances, get_instance_by_api_id
from shared.crud.session import get_session_by_hash

from hashlib import sha256

from admin.utils.db import async_session_maker


router = APIRouter()
manager = WSManager()
user_manager = WSManager()
chat_manager = ChatWSManager()


@router.websocket("/ws/chat/{api_id}/{chat_id}")
async def chat_ws(ws: WebSocket, api_id: int, chat_id: str):
    token = ws.cookies.get("g-session")
    if not token:
        return await ws.close(code=status.WS_1008_POLICY_VIOLATION)

    async with async_session_maker() as db:
        inst = await get_instance_by_api_id(db, api_id=api_id)
        sess = await get_session_by_hash(db, token_hash=sha256(token.encode()).hexdigest())
        user = sess.user if sess else None
        if not inst or not user or not has_instance_access(user, inst):
            return await ws.close(code=status.WS_1008_POLICY_VIOLATION)

    await chat_manager.connect(ws, inst.id, chat_id)
    try:
        while True:
            await ws.receive_text()
    except Exception:
        chat_manager.disconnect(ws)


@router.websocket("/ws/users")
async def ws_users(ws: WebSocket):
    token = ws.cookies.get("g-session")
    if not token:
        return await ws.close(code=status.WS_1008_POLICY_VIOLATION)

    token_hash = sha256(token.encode()).hexdigest()
    async with async_session_maker() as db:
        sess = await get_session_by_hash(db, token_hash=token_hash)
        if not sess or sess.is_expired() or not sess.is_active:
            return await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        user = sess.user

    allowed = None if can_manage_users(user) else {user.id}

    await user_manager.connect(ws, allowed)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        user_manager.disconnect(ws)


@router.websocket("/ws/instances")
async def ws_instances(ws: WebSocket):
    token = ws.cookies.get("g-session")
    if not token:
        return await ws.close(code=status.WS_1008_POLICY_VIOLATION)

    token_hash = sha256(token.encode()).hexdigest()

    async with async_session_maker() as db:
        sess = await get_session_by_hash(db, token_hash=token_hash)
        user = sess.user
        if not sess or not user or not sess.is_active or sess.is_expired():
            return await ws.close(code=status.WS_1008_POLICY_VIOLATION)

    allowed_ids = None
    if not (user.full_access or user.is_owner):
        insts = await list_instances(db, user=user)
        allowed_ids = {i.id for i in insts}

    await manager.connect(ws, allowed_ids)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
