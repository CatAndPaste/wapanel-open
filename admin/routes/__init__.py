from fastapi import FastAPI

from .auth import router as auth_router
from .websockets import router as websockets_router
from .misc import router as misc_router
from .instances import router as instances_router
from .users import router as users_router
from .chat import router as chat_router
from .chats import router as chats_router
from .chats_sidebar import router as chats_sidebar_router


def register_all_routers(app: FastAPI) -> None:
    app.include_router(auth_router)
    app.include_router(misc_router)
    app.include_router(instances_router)
    app.include_router(users_router)
    app.include_router(chat_router)
    app.include_router(chats_router)
    app.include_router(chats_sidebar_router)
    app.include_router(websockets_router, tags=["WebSocket"])
