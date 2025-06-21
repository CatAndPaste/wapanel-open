from __future__ import annotations

import asyncio
import json
from typing import Final

import asyncpg

from app.green_api.manager import ClientManager
from app.loader import logger
from app.utils.config import settings


# payload types
_INSERT: Final = {"insert", "update"}
_DELETE: Final = {"delete"}


async def instance_listener(
    manager: ClientManager,
    stop: asyncio.Event,
) -> None:
    """
    On instances change.

    - insert / update -> manager.refresh_instance(row_id)
    - delete -> manager.drop_client(api_id)
    """
    pg = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_db,
        host=settings.postgres_host,
        port=settings.postgres_port,
    )

    async def _handler(_, __, ___, payload: str) -> None:
        try:
            data = json.loads(payload)
            action: str = data["action"]
        except (json.JSONDecodeError, KeyError):
            logger.warning("instance_listener: malformed payload «%s»", payload)
            return

        if action in _INSERT:
            row_id = data["id"]
            asyncio.create_task(manager.refresh_instance(row_id))
            logger.debug("instance_listener: refresh %s (%s)", row_id, action)

        elif action in _DELETE:
            api_id = data["api_id"]
            asyncio.create_task(manager.drop_client(api_id))
            logger.debug("instance_listener: drop %s", api_id)

        else:
            logger.warning("instance_listener: unknown action «%s»", action)

    await pg.add_listener("instance_change", _handler)
    logger.info("LISTEN instance_change — started")

    try:
        await stop.wait()                      # sleeps until done
    finally:
        await pg.remove_listener("instance_change", _handler)
        await pg.close()
        logger.info("LISTEN instance_change — stopped")
