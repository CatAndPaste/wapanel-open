import hashlib
import hmac
import os
import time

from aiogram.enums import ParseMode
from aiohttp import web
from app.loader import bot, logger
from app.utils.channels import sync_channel_record
from app.utils.db import async_session_maker

routes = web.RouteTableDef()


@routes.post("/admin/send_message")
async def admin_send_message(request: web.Request) -> web.Response:
    data = await request.json()
    user_id = data.get("user_id")
    text = data.get("text")
    use_markdown = data.get("use_markdown") or False

    if not user_id or not text:
        return web.json_response({"status": "fail",
                                  "detail": "недостаточно данных (user_id, text)"},
                                 status=200)

    try:
        await bot.send_message(
            chat_id=int(user_id),
            text=text,
            parse_mode=ParseMode.MARKDOWN if use_markdown else ParseMode.HTML,
        )
    except Exception as e:
        logger.error("Ошибка при отправке сообщения: %s", e)
        return web.json_response({"status": "fail",
                                  "detail": str(e)},
                                 status=200)

    return web.json_response({"status": "ok"}, status=200)


@routes.post("/admin/update_channel")
async def admin_update_channel(request: web.Request) -> web.Response:
    """
    { "tg_id": -1001234567890 }
    Calls sync_channel_record for channel with ID provided
    """
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    tg_id = payload.get("tg_id")
    if tg_id is None:
        return web.json_response({"error": "tg_id is required"}, status=400)

    try:
        tg_id = int(tg_id)
    except ValueError:
        return web.json_response({"error": "tg_id must be integer"}, status=400)

    # sync
    try:
        async with async_session_maker() as session:
            await sync_channel_record(bot, tg_id, session=session)
        logger.info("Channel %s synced via /admin/update_channel", tg_id)
        return web.json_response({"status": "ok"})
    except Exception as e:
        logger.exception("sync_channel_record failed: %s", e)
        return web.json_response({"status": "error", "detail": str(e)}, status=500)
