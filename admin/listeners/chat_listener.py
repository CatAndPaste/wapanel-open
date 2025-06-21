import asyncpg, asyncio, json
from admin.utils.config import settings
from admin.websockets.manager import ChatWSManager
from admin.utils.logger import logger


async def msg_change_listener(stop: asyncio.Event, manager: ChatWSManager):
    conn = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_db,
        host=settings.postgres_host,
        port=settings.postgres_port,
    )

    async def _cb(*args):
        await manager.broadcast(args[3])

    await conn.add_listener("msg_change", _cb)
    logger.info("LISTEN msg_change – started")
    try:
        await stop.wait()
    finally:
        await conn.remove_listener("msg_change", _cb)
        await conn.close()
        logger.info("LISTEN msg_change – stopped")
