import os, asyncio, logging
from asyncio import sleep

from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.loader import logger, app as web_app, bot, dp

from aiogram.types import BotCommandScopeDefault
from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from app.utils.channels import sync_channel_record
from app.utils.commands import CMDS, SHORT_DESC, FULL_DESC
from app.utils.config import settings
from app.utils.db import async_session_maker
from app.green_api.manager import ClientManager
from app.telegram_bot.handlers import register_all_handlers
from app.routes import setup_routes
from app.telegram_bot.middleware.session_middleware import DBSessionMiddleware
from app.utils.triggers import init_triggers_pg

from app.listeners.msg_in_listener import msg_inbox
from app.listeners.msg_out_listener import msg_outbox
from app.listeners.instance_change_listener import instance_listener
from shared.models import TelegramChannel, BotMeta

# uvloop doesn't support Windows
# (for dev purposes)
try:
    import uvloop
except ImportError:
    uvloop = None
    pass

# ========
# aiogram
# ========

TOKEN = os.getenv("TG_TOKEN")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_HOST")  # wapanel.ru
WEBHOOK_PREFIX = f"/bot"
WEBHOOK_PATH = f"/tg-webhooks-8008"
LISTEN_PORT = int(os.getenv("WEBHOOK_PORT", 8008))

# adding middleware to bot updates
dp.update.outer_middleware(DBSessionMiddleware())

# registering handlers
register_all_handlers(dp)

# ========
# aiohttp
# ========

# adding internal routes
setup_routes(web_app)
# aiogram webhook-handler
SimpleRequestHandler(dp, bot).register(web_app, path=WEBHOOK_PATH)


async def set_bot_meta():
    await bot.set_my_commands(CMDS, scope=BotCommandScopeDefault())
    await bot.set_my_short_description(SHORT_DESC)
    await bot.set_my_description(FULL_DESC)


async def sync_bot_info(session: AsyncSession):
    row = (await session.execute(select(BotMeta))).scalars().first()
    try:
        me = await bot.get_me()
        if not row:
            row = BotMeta(
                bot_id=me.id,
                username=me.username,
                first_name=me.first_name,
                is_active=True
            )
        else:
            row.bot_id = me.id
            row.username = me.username
            row.first_name = me.first_name
            row.is_active = True
    except Exception as e:
        logger.error(f"Couldn't get bot info: {e}")
        if not row:
            row = BotMeta(
                is_active=False
            )
        else:
            row.bot_id = None
            row.username = None
            row.first_name = None
            row.is_active = False
    session.add(row)
    await session.commit()
    await session.refresh(row)
    if row.is_active:
        logger.info(f"Bot id: {row.bot_id} {row.first_name} (@{row.username})")
    else:
        logger.error("Seems like Bot is down!")


async def start_listeners():
    # instances
    web_app["inst_stop"] = asyncio.Event()
    web_app["inst_task"] = asyncio.create_task(instance_listener(web_app["client_manager"], web_app["inst_stop"]))
    # messages
    web_app["in_stop"] = asyncio.Event()
    web_app["in_task"] = asyncio.create_task(msg_inbox(web_app["in_stop"]))
    web_app["out_stop"] = asyncio.Event()
    web_app["out_task"] = asyncio.create_task(msg_outbox(web_app["out_stop"]))


async def stop_listeners():
    web_app["inst_stop"].set()
    await web_app["inst_task"]
    web_app["in_stop"].set()
    await web_app["in_task"]
    web_app["out_stop"].set()
    await web_app["out_task"]


async def _set_webhook():
    backoff = 5
    while True:
        try:
            await bot.set_webhook(f"https://{WEBHOOK_BASE_URL}{WEBHOOK_PREFIX}{WEBHOOK_PATH}",
                                  drop_pending_updates=True)
            logger.info(f"Webhook set to {WEBHOOK_BASE_URL}{WEBHOOK_PREFIX}{WEBHOOK_PATH}")
            break
        except TelegramBadRequest as e:
            logger.warning("Can't set Telegram webhook (%s). "
                           "Retry in %s s …", e, backoff)
        except Exception as e:
            logger.exception("Unexpected error on set_webhook: %s", e)

        await sleep(backoff)
        backoff = min(backoff * 2, 300)


# lifespan
async def on_startup(_):
    # Telegram Bot
    await _set_webhook()
    await set_bot_meta()

    # DB Triggers
    try:
        await init_triggers_pg(settings)
        logger.info("Instance triggers created successfully")
    except Exception as e:
        logger.error(f"Error while creating trigger for instances: {e}")

    # sync telegram channels & bot info
    logger.info("Начинаю синхронизацию записей TelegramChannel...")
    async with async_session_maker() as session:  # type: AsyncSession
        await sync_bot_info(session)

        # 1) достаём все каналы (можно фильтровать по is_active, если хотите)
        channels = (await session.execute(
            select(TelegramChannel)
        )).scalars().all()

        # 2) для каждого — синхронизируем
        for chan in channels:
            tg_id = chan.telegram_id
            logger.info(f"  Проверяю канал {chan.id} (tg_id={tg_id}) …")
            try:
                await sync_channel_record(bot, tg_id, session=session)
                logger.info(f"    → канал {tg_id} синхронизирован, is_active={chan.is_active}")
            except Exception as e:
                logger.error(f"    ! не удалось синхронизировать канал {tg_id}: {e}")

    # Green API Client Manager
    web_app["client_manager"] = ClientManager(async_session_maker, logger)
    await web_app["client_manager"].start()

    # listeners
    await start_listeners()
    logger.info(f"App started")


async def on_shutdown(_):
    await web_app["client_manager"].close()
    await stop_listeners()
    await bot.delete_webhook()
    await bot.session.close()
    logger.info("App stopped")


setup_application(web_app, dp, bot=bot)
web_app.on_startup.append(on_startup)
web_app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    if uvloop is not None:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    else:
        logger.warning("uvloop is not available!")
    web.run_app(web_app, host="0.0.0.0", port=LISTEN_PORT)
