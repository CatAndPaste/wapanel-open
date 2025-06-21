from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.models import TelegramChannel


async def get_or_create(
    session: AsyncSession,
    telegram_id: int,
    defaults: Optional[Dict[str, Any]] = None
) -> TelegramChannel:
    """
    Gets or creates TelegramChannel object.
    Example:
    channel = await get_or_create(session, telegram_id=-88005553535, defaults={
        "name": "Test Channel"
    })
    """
    q = select(TelegramChannel).where(TelegramChannel.telegram_id == telegram_id)
    result = await session.execute(q)
    channel = result.scalars().first()
    if channel:
        return channel

    params = {"telegram_id": telegram_id}
    if defaults:
        params.update(defaults)

    channel = TelegramChannel(**params)
    session.add(channel)
    await session.commit()
    await session.refresh(channel)
    return channel


async def update_channel(
    session: AsyncSession,
    telegram_id: int,
    **kwargs: Any
) -> Optional[TelegramChannel]:
    """
    Updates TelegramChannel object. If TelegramChannel doesn't exist, returns None, otherwise - updated object.
    Example:
    channel = await update_channel(session, telegram_id=-88005553535, name="Name from Telegram API")
    """
    q = select(TelegramChannel).where(TelegramChannel.telegram_id == telegram_id)
    result = await session.execute(q)
    channel = result.scalars().first()
    if not channel:
        return None

    for field, value in kwargs.items():
        if hasattr(channel, field):
            setattr(channel, field, value)

    await session.commit()
    await session.refresh(channel)
    return channel


async def delete_channel(
    session: AsyncSession,
    telegram_id: int
) -> bool:
    """
    Deletes TelegramChannel object. If deleted, returns True, otherwise - False.
    """
    q = select(TelegramChannel).where(TelegramChannel.telegram_id == telegram_id)
    result = await session.execute(q)
    channel = result.scalars().first()
    if not channel:
        return False

    await session.delete(channel)
    await session.commit()
    return True


async def get_channel(
    session: AsyncSession,
    telegram_id: int
) -> Optional[TelegramChannel]:
    """
    Returns TelegramChannel object or None if none found.
    """
    q = select(TelegramChannel).where(TelegramChannel.telegram_id == telegram_id)
    result = await session.execute(q)
    return result.scalars().first()
