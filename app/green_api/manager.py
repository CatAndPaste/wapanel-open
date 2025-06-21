from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .client import GreenAPIClient
from .exceptions import GreenAPIThrottleError
from shared.models import Instance, InstanceState
from app.utils.config import settings
from ..loader import logger


class ClientManager:
    """
    Caches GreenAPIClients (key - api_id)
    Single aiohttp session
    Syncs with DB
    """

    def __init__(self, db_factory: async_sessionmaker[AsyncSession], logger) -> None:
        self._db_factory = db_factory
        self._log = logger
        self._clients: dict[int, GreenAPIClient] = {}
        self._session: aiohttp.ClientSession | None = None
        self._sync_task: asyncio.Task | None = None
        self._history_lock: dict[int, asyncio.Lock] = {}

    # public

    async def start(self) -> None:
        """Creates clients based on DB rows and start background sync"""
        await self._ensure_session()
        await self._sync_with_db()

    async def close(self) -> None:
        if self._sync_task:
            self._sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._sync_task
        if self._session and not self._session.closed:
            await self._session.close()

    async def refresh_instance(self, row_id: int) -> None:
        """
        event-driver on INSERT/UPDATE of row_id instance
        """
        try:
            async with self._db_factory() as db:
                inst = await db.get(Instance, row_id)
                if inst is None:
                    self._log.warning("Row %s disappeared before bootstrap", row_id)
                    return
                api_id = inst.api_id
            await self._bootstrap(api_id)

        except Exception as e:
            self._log.error("refresh_instance(%s) failed → %s", row_id, e)

    async def refresh_instance_by_api_id(self, api_id: int) -> None:
        await self._bootstrap(api_id)

    async def drop_client(self, api_id: int) -> None:
        """
        event-driven on DELETE, forgets instance: removes webhook on GAPI side (best-effort), clears cache
        """
        cli = self._clients.pop(api_id, None)
        if cli is None:
            return  # nothing to see here o.o

        self._log.info("Drop client %s (DELETE row)", api_id)
        # best-effort
        with suppress(Exception):
            await self._clear_webhook(cli, api_id)

    @asynccontextmanager
    async def get_client(self, api_id: int) -> AsyncIterator[GreenAPIClient]:
        if api_id not in self._clients:
            await self._bootstrap(api_id)  # on request
        yield self._clients[api_id]

    # internals

    async def _ensure_session(self) -> None:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()

    async def _sync_with_db(self) -> None:
        async with self._db_factory() as db:
            rows = await db.scalars(select(Instance))
            actual = {r.api_id: r for r in rows}

        for api_id in set(self._clients) - set(actual):
            self._log.info("Drop client %s (row removed)", api_id)
            self._clients.pop(api_id, None)

        for inst in actual.values():
            await self._bootstrap(inst.api_id, fresh=inst)

    # create / update client
    async def _bootstrap(
            self,
            api_id: int,
            *,
            fresh: Instance | None = None,
    ) -> None:
        await self._ensure_session()

        if fresh is None:
            async with self._db_factory() as db:
                fresh = await db.scalar(select(Instance).where(Instance.api_id == api_id).limit(1))
            if fresh is None:
                self._log.warning("Instance %s disappeared before bootstrap", api_id)
                return

        fp = (fresh.api_url, fresh.media_url, fresh.api_token)
        cached = self._clients.get(api_id)

        if cached and cached._fingerprint == fp:
            await self._sync_state(cached, fresh)
            return

        # clearing webhook if instance id is changed
        with suppress(Exception):
            await self._clear_webhook(cached, api_id)

        client = GreenAPIClient(
            api_url=fresh.api_url,
            media_url=fresh.media_url,
            id_instance=fresh.api_id,
            token=fresh.api_token,
            session=self._session,
        )
        client._fingerprint = fp  # quick checkers
        self._clients[api_id] = client

        await self._sync_state(client, fresh)

    # update Instance.state / phone / avatar
    async def _sync_state(self, cli: GreenAPIClient, row: Instance) -> None:
        modified = False
        try:
            state = InstanceState(await cli.get_state())
        except GreenAPIThrottleError:
            self._log.warning("429 on get_state for %s", row.api_id)
            return
        except Exception as e:
            self._log.error("get_state failed for %s → %s", row.api_id, e)
            state = InstanceState.unknown

        if row.state != state:
            row.state = state
            modified = True

        if state == InstanceState.authorized:
            try:
                wa = await cli.get_settings()
                phone = wa.get("wid").rsplit("@", 1)[0]
                if row.phone != phone:
                    row.phone = phone
                    modified = True
                """if row.photo_url != "photo_url":
                    row.photo_url = "photo_url"
                    modified = True"""
            except Exception as e:
                self._log.error("get_settings failed for %s: %s", row.api_id, e)

        else:  # not authorised -> clears the data
            if row.phone or row.photo_url:
                row.phone = row.photo_url = None
                modified = True

        # webhook setup guaranteed if GAPI is available
        if state != InstanceState.unknown:
            await self._ensure_webhook(cli, row)

        if modified:
            async with self._db_factory() as db:
                db.add(row)
                await db.commit()

    # context manager
    @asynccontextmanager
    async def history_lock(self, api_id: int):
        lock = self._history_lock.setdefault(api_id, asyncio.Lock())
        if lock.locked():
            raise RuntimeError("history task already running for this instance")
        async with lock:
            yield

    # webhook
    async def _ensure_webhook(self, g_client: GreenAPIClient, row: Instance) -> None:
        try:
            st = await g_client.get_settings()
            if (
                    st["webhookUrl"] != settings.GREEN_WEBHOOK_PUBLIC
                    # webhook notifications
                    or st["incomingWebhook"] != "yes"
                    or st["outgoingWebhook"] != "yes"
                    or st["outgoingMessageWebhook"] != "yes"
                    or st["outgoingAPIMessageWebhook"] != "yes"
                    or st["stateWebhook"] != "yes"
                    or st["incomingCallWebhook"] != "yes"
                    # general settings
                    or st["markIncomingMessagesReadedOnReply"] != "yes"
            ):
                if await g_client.set_settings(
                        {
                            "webhookUrl": settings.GREEN_WEBHOOK_PUBLIC,
                            "incomingWebhook": "yes",
                            "outgoingWebhook": "yes",
                            "outgoingMessageWebhook": "yes",
                            "outgoingAPIMessageWebhook": "yes",
                            "stateWebhook": "yes",
                            "incomingCallWebhook": "yes",
                            "markIncomingMessagesReadedOnReply": "yes"
                        }
                ):
                    logger.info(f"Settings updated for instance {row.api_id}")
                else:
                    logger.error(f"Could not update settings for instance {row.api_id}")
            else:
                logger.info(f"Settings up to date for instance {row.api_id}")
        except Exception as e:
            logger.error(f"ensure_webhook failed for {row.api_id}: {e}")

    async def _clear_webhook(self, cli: GreenAPIClient, api_id: int) -> None:
        try:
            if await cli.set_settings(
                {
                    "webhookUrl": "",
                    "incomingWebhook": "no",
                    "outgoingWebhook": "no",
                    "outgoingMessageWebhook": "no",
                    "outgoingAPIMessageWebhook": "no",
                    "stateWebhook": "no",
                    "incomingCallWebhook": "no",
                    "markIncomingMessagesReadedOnReply": "no"
                }
            ):
                logger.info(f"Settings reset for instance {api_id}")
            else:
                logger.error(f"Could not reset settings for instance {api_id}")
        except Exception as e:
            self._log.error("clear_webhook failed for %s: %s", api_id, e)
