from __future__ import annotations

import asyncio, random
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Final

import aiohttp
from aiolimiter import AsyncLimiter
from pydantic import TypeAdapter, BaseModel

from .exceptions import GreenAPIError, GreenAPIThrottleError
from .limiter import SmartLimiter
from .limits import _METHOD_RPS, DEFAULT_RPS
from ..loader import logger

_json = dict[str, Any]

_TIMEOUT = 30
_MEDIA_TIMEOUT = 60


class GreenAPIClient:
    """
    GreenAPI Client, InstanceManager is the factory
    Do not create manual instances!
    """

    # infra
    def __init__(
            self,
            *,
            api_url: str,
            media_url: str,
            id_instance: int,
            token: str,
            session: aiohttp.ClientSession,
    ) -> None:
        self.api_root = api_url.rstrip("/")
        self.media_root = media_url.rstrip("/")
        self.id = id_instance
        self.tok = token
        self._session = session

        self._limiters: dict[str, SmartLimiter] = {}

        # quick checkers
        self._fingerprint: tuple[str, str, str] | None = None  # api_url, media_url, token

        # qr
        self._qr_lock: asyncio.Lock = asyncio.Lock()
        self._qr_cache: dict[str, tuple[dict, float]] = {}

    # public API

    # qr
    async def get_qr(self) -> dict[str, Any]:
        """
        Returns "status": "...", "image": "...", "message": "..."}
        Throttles GAPI requests at 1 req per seoncd
        """

        now = asyncio.get_running_loop().time()
        cache_key = "qr"
        cached, ts = self._qr_cache.get(cache_key, (None, 0))

        if now - ts < 1:
            return cached

        async with self._qr_lock:  # concurrency protection
            cached, ts = self._qr_cache.get(cache_key, (None, 0))
            if now - ts < 1:
                return cached

            try:
                raw = await self._json("qr")
            except GreenAPIThrottleError:
                self._limiters["qr"].block()
                return {"status": "error",
                        "message": "too many requests (429)"}
            except GreenAPIError as e:
                return {"status": "error", "message": str(e)}

            resp_type = raw.get("type")
            if resp_type == "qrCode":
                payload = {"status": "qr",
                           "image": f"data:image/png;base64,{raw['message']}"}
            elif resp_type == "alreadyLogged":
                payload = {"status": "already_logged"}
            elif resp_type == "timeout":
                payload = {"status": "timeout"}
            else:
                payload = {"status": "error",
                           "message": raw.get("message", "unknown")}

            self._qr_cache[cache_key] = (payload, now)
            return payload

    async def get_settings(self) -> _json:
        return await self._json("getSettings")

    async def get_state(self) -> str:
        return (await self._json("getStateInstance"))["stateInstance"]

    async def logout(self) -> bool:
        return bool((await self._json("logout")).get("isLogout"))

    async def set_settings(self, data: _json) -> bool:
        return bool((await self._json("setSettings", "POST", json=data)).get("saveSettings"))

    async def last_incoming(self, *, minutes: int) -> list[dict]:
        return await self._json("lastIncomingMessages", params={"minutes": minutes})

    async def last_outgoing(self, *, minutes: int) -> list[dict]:
        return await self._json(f"lastOutgoingMessages", params={"minutes": minutes})

    async def get_chat_history(self, chat_id: str, *, count: int = 100) -> list[dict]:
        return await self._json(
            "getChatHistory",
            "POST",
            json={"chatId": chat_id, "count": count},
        )

    async def download_file(self, *, chat_id: str, id_message: str) -> str | None:
        try:
            resp = await self._json(
                "downloadFile", "POST",
                json={"chatId": chat_id, "idMessage": id_message}
            )
            return resp.get("downloadUrl") or None
        except GreenAPIError as e:
            logger.warning("downloadFile failed (%s, %s): %s", chat_id, id_message, e)
            return None

    async def send_message(self, *, chat_id: str, text: str) -> _json:
        return await self._json("sendMessage", "POST", json={"chatId": chat_id, "message": text})

    async def send_file(
            self,
            *,
            chat_id: str,
            file_bytes: bytes,
            filename: str,
            mime: str,
            caption: str = "",
    ) -> _json:
        form = aiohttp.FormData()
        form.add_field("chatId", chat_id)
        form.add_field("fileName", filename)
        form.add_field("caption", caption)
        form.add_field("file", file_bytes, filename=filename, content_type=mime)

        return await self._json(
            "sendFileByUpload",
            "POST",
            data=form,
            media=True,
        )

    # low-level helpers

    async def _json(
            self,
            endpoint: str,
            http: str = "GET",
            *,
            params: _json | None = None,
            json: _json | None = None,
            data: aiohttp.FormData | None = None,
            media: bool = False
    ) -> Any:
        root = self.media_root if media else self.api_root
        url = f"{root}/waInstance{self.id}/{endpoint}/{self.tok}"

        base = endpoint.split("/")[0]  # remove closing /
        limiter = self._get_limiter(base)

        async with limiter:
            async with self._session.request(http, url, params=params, json=json, data=data, timeout=_TIMEOUT) as r:
                if r.status == 429:
                    limiter.block()
                    raise GreenAPIThrottleError("429 Too Many Requests")

                if r.status >= 400:
                    raise GreenAPIError(f"GREEN-API {r.status}: {await r.text()}")

                result = await r.json()

        return result

    # per-instance rate-limit + backoff after 429
    def _get_limiter(self, method: str) -> SmartLimiter:
        base = method.split("/")[0]
        if base not in self._limiters:
            rps = _METHOD_RPS.get(base, DEFAULT_RPS)
            self._limiters[base] = SmartLimiter(rps)
        return self._limiters[base]

    @property
    def session(self) -> aiohttp.ClientSession:
        return self._session
