import asyncio
import json, hmac, hashlib, time, os
from aiohttp import web
from aiohttp.web_exceptions import HTTPException, HTTPBadRequest

from app.loader import logger, bot
from app.green_api.manager import ClientManager
from app.utils.history_downloader import load_history


routes = web.RouteTableDef()

_COOLDOWN: dict[int, float] = {}
TTL = 60

@routes.post(r"/admin/instance/{api_id:\d+}/refresh")
async def rpc_refresh(request: web.Request) -> web.Response:
    api_id = int(request.match_info["api_id"])
    now = time.time()
    last = _COOLDOWN.get(api_id, 0)

    if now - last < TTL:
        return web.json_response(
            {"error": "cooldown"},
            status=429,
        )

    _COOLDOWN[api_id] = now

    im: ClientManager = request.app["client_manager"]

    async def _job():
        try:
            await im.refresh_instance_by_api_id(api_id)
        except Exception as e:
            logger.error("refresh_instance(%s) failed: %s", api_id, e)

    asyncio.create_task(_job())
    return web.json_response({"status": "scheduled"}, status=202)


@routes.post(r"/admin/instance/{inst_id:\d+}/logout")
async def rpc_logout(request: web.Request):
    raw = await request.read()

    inst_id = int(request.match_info["inst_id"])
    im: ClientManager = request.app["client_manager"]

    async with im.get_client(inst_id) as client:
        return web.json_response({"status": "ok", "isLogout": await client.logout()})


@routes.post(r"/admin/instance/{inst_id:\d+}/qr")
async def rpc_get_qr(request: web.Request):
    raw = await request.read()

    inst_id = int(request.match_info["inst_id"])
    im: ClientManager = request.app["client_manager"]

    async with im.get_client(inst_id) as client:
        payload = await client.get_qr()
        return web.json_response(payload)


from aiohttp import web
from app.utils.history_downloader import load_history, history_lock
from app.loader import app, logger


@routes.post(r"/admin/history/{api_id:\d+}")
async def admin_history(request: web.Request) -> web.Response:
    api_id = int(request.match_info["api_id"])

    # wait_authorized
    q_wait = request.rel_url.query.get("wait_authorized")
    wait = str(q_wait).lower() in {"1", "true", "yes"}

    # json
    if not wait and request.can_read_body:
        try:
            body = await request.json()
            wait = bool(body.get("wait_authorized", False))
        except Exception as exc:
            raise HTTPBadRequest(text=f"invalid json: {exc}") from None

    # single-instance lock
    lock = history_lock.setdefault(api_id, asyncio.Lock())
    if lock.locked():
        return web.json_response({"error": "already_running"}, status=409)

    await lock.acquire()

    async def _task():
        try:
            await load_history(request.app, api_id, wait_authorized=wait)
        except Exception as e:
            logger.exception("history[%s] failed: %s", api_id, e)
        finally:
            lock.release()

    asyncio.create_task(_task())
    return web.json_response({"status": "scheduled"}, status=201)
