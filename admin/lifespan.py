import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select, literal

from admin.listeners.chat_listener import msg_change_listener as chat_listener
from admin.listeners.instance_listener import instance_listener
from admin.listeners.user_listener import user_listener
from admin.utils.triggers import init_triggers_pg
from admin.utils.config import settings
from admin.utils.db import async_session_maker
from shared.crud.user import get_user_by_username, create_user
from admin.routes.websockets import manager as instance_ws_manager, user_manager as user_ws_manager, chat_manager as chat_ws_manager
from shared.models import User


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    try:
        await init_triggers_pg(settings)
        print("Триггер notify_new_message создан (или обновлён)")
    except Exception as e:
        print(f"Не удалось создать триггер в БД: {e}")
        raise

    # db events
    stop_event = asyncio.Event()
    app.state.stop_event = stop_event  # type: ignore[attr-defined]
    app.state.listener_task = asyncio.create_task(  # type: ignore[attr-defined]
        instance_listener(stop_event, instance_ws_manager)
    )
    user_stop_event = asyncio.Event()
    app.state.user_stop_event = user_stop_event  # type: ignore[attr-defined]
    app.state.user_listener_task = asyncio.create_task(  # type: ignore[attr-defined]
        user_listener(user_stop_event, user_ws_manager)
    )
    chat_stop_event = asyncio.Event()
    app.state.chat_stop_event = chat_stop_event  # type: ignore[attr-defined]
    app.state.chat_listener_task = asyncio.create_task(  # type: ignore[attr-defined]
        chat_listener(chat_stop_event, chat_ws_manager)
    )
    print("Запущен pg_listener() в фоновом режиме")

    async with async_session_maker() as session:
        owner_exists = await session.scalar(
            select(literal(True)).where(User.is_owner).limit(1)
        )

        if not owner_exists:
            print("creating owner with reqs admin : ps97m329f")
            try:
                new_user = await create_user(session=session,
                                             username="alsk",
                                             telegram_id=45543954,
                                             password="admin",
                                             is_owner=True,
                                             is_2fa_enabled=True,
                                             can_manage_users=True,
                                             can_manage_instances=True,
                                             full_access=True)
            except Exception as e:
                print(e)
                pass

    # life cycle
    yield

    # shutdown
    stop_event.set()
    await app.state.listener_task  # type: ignore[attr-defined]
    user_stop_event.set()
    await app.state.user_listener_task  # type: ignore[attr-defined]
    chat_stop_event.set()
    await app.state.chat_listener_task  # type: ignore[attr-defined]
