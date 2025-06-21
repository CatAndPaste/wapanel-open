import asyncio, asyncpg, json
from admin.utils.config import settings
from admin.websockets.manager import WSManager
from admin.utils.logger import logger

async def user_listener(stop_event: asyncio.Event, manager: WSManager):
    conn = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_db,
        host=settings.postgres_host,
        port=settings.postgres_port,
    )

    async def _handler(_, pid, channel, payload):
        await manager.broadcast(payload)

    await conn.add_listener("user_change", _handler)
    logger.info("LISTEN user_change – started")

    try:
        await stop_event.wait()
    finally:
        await conn.remove_listener("user_change", _handler)
        await conn.close()
        logger.info("LISTEN user_change – stopped")