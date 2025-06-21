import os
from aiohttp import web

from app.utils.config import settings

PREFIX = ("/admin/",)


@web.middleware
async def check_admin_token(request: web.Request, handler):
    if request.path.startswith(PREFIX):
        if request.headers.get("X-Admin-Token") != settings.ADMIN_RPC_TOKEN:
            raise web.HTTPUnauthorized(text="invalid or missing token")
    return await handler(request)