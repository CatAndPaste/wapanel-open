import os
import hmac
import hashlib
import time
import json
import httpx
from typing import Any

from admin.utils.config import settings
from admin.utils.logger import logger


async def _async_post(path: str, payload: dict | None = None, timeout: float = 5.0) -> Any:
    """
    async POST with HMAC
    Throws httpx.HTTPError
    """
    payload = payload or {}
    headers = {"X-Admin-Token": settings.ADMIN_RPC_TOKEN}
    url = f"{settings.BOT_URL}{path}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.NetworkError) as exc:
        logger.warning("Bot unavailable: %s %s", url, exc.__class__.__name__)
        return None


async def logout_instance(api_id: int) -> bool:
    """
    Returns True on success
    """
    data = await _async_post(f"/admin/instance/{api_id}/logout")
    return data.get("isLogout", False)


async def get_qr(api_id: int) -> dict | None:
    """
    Requests /qr from bot container, returns:
        {"status":"qr", "image":"data:image/png;base64,..."}
        {"status":"already_logged"}
        {"status":"timeout"}
        {"status":"error","message":"..."}
    """
    try:
        return await _async_post(f"/admin/instance/{api_id}/qr")
    except Exception:
        return None


async def update_channel(tg_id: int) -> bool:
    """
    Sync bot
    """
    data = await _async_post("/admin/update_channel", {"tg_id": tg_id})
    return data.get("status") == "ok"


async def send_notification(user_id: int, text: str, use_markdown: bool = False) -> bool:
    payload = {"user_id": user_id, "text": text, "use_markdown": use_markdown}
    try:
        data = await _async_post("/admin/send_message", payload)
    except httpx.HTTPStatusError as exc:
        logger.warning("send_notification: HTTPStatusError %s", exc)
        return False
    return bool(data and data.get("status") == "ok")


async def send_message(user_id: int, text: str) -> bool:
    """
    No Markdown wrapper for send_notification
    """
    return await send_notification(user_id, text, use_markdown=False)


async def start_history(api_id: int, *, wait_authorized: bool = False) -> bool:
    """/admin/history/<api_id>"""
    qs = "?wait_authorized=1" if wait_authorized else ""
    try:
        await _async_post(f"/admin/history/{api_id}{qs}", {})
        return True
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:   # in progress
            return False
        raise


async def refresh_instance(api_id: int) -> str | bool:
    """
    Requests instance update, returns:
    True - task enqueued
    "cooldown" -> bot-side TTL (60s)
    False - other error
    """
    try:
        data = await _async_post(f"/admin/instance/{api_id}/refresh")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            return "cooldown"
        return False
    return data.get("status") == "scheduled"
