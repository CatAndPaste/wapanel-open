from typing import Optional, List, Any
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.models import Instance, User
from shared.crud.channel import get_or_create as get_or_create_channel


async def list_instances(
    session: AsyncSession,
    *, user: User
) -> List[Instance]:
    """
    Returns all instances (with telegram channels loaded)
    """
    if user.full_access or user.is_owner:
        q = select(Instance).options(selectinload(Instance.telegram_channel))
    else:
        q = (select(Instance)
             .join(Instance.users)
             .where(User.id == user.id)
             .options(selectinload(Instance.telegram_channel))
             )
    return list((await session.execute(q)).scalars().all())


async def get_instance_by_id(
    session: AsyncSession,
    *,
    instance_id: int
) -> Optional[Instance]:
    """
    Get Instance by internal ID
    """
    stmt = (
        select(Instance)
        .options(selectinload(Instance.telegram_channel))
        .where(Instance.id == instance_id)
    )
    result = await session.execute(stmt)
    return result.scalars().first()


async def get_instance_by_api_id(
    session: AsyncSession,
    *,
    api_id: int
) -> Optional[Instance]:
    """
    Get instance by API_ID
    """
    stmt = (
        select(Instance)
        .options(selectinload(Instance.telegram_channel))
        .where(Instance.api_id == api_id)
    )
    result = await session.execute(stmt)
    return result.scalars().first()


async def create_instance(
    session: AsyncSession,
    *,
    api_id: int,
    api_url: str,
    media_url: str,
    api_token: str,
    telegram_channel_tg_id: int,
    auto_reply: bool = False,
    auto_reply_text: Optional[str] = None,
    inst_name: Optional[str] = None,
) -> Instance:
    """
    Create new Instance and TelegramChannel if needed
    :telegram_channel_tg_id: Telegram Channel ID
    """
    channel = await get_or_create_channel(
        session,
        telegram_id=telegram_channel_tg_id,
        defaults={}
    )

    inst = Instance(
        name=inst_name,
        api_id=api_id,
        api_url=api_url,
        media_url=media_url,
        api_token=api_token,
        telegram_channel=channel,
        auto_reply=auto_reply,
        auto_reply_text=auto_reply_text,
    )
    session.add(inst)
    await session.commit()
    await session.refresh(inst)
    return inst


async def update_instance(
    session: AsyncSession,
    instance: Instance,
    **kwargs: Any
) -> Instance:
    """
    Updates Instance
    :telegram_channel_tg_id: Telegram Channel ID (Optional)
    """
    tg_id = kwargs.pop("telegram_channel_tg_id", None)
    if tg_id is not None:
        channel = await get_or_create_channel(session, telegram_id=tg_id, defaults={})
        instance.telegram_channel = channel

    for field, value in kwargs.items():
        if hasattr(instance, field):
            setattr(instance, field, value)

    session.add(instance)
    await session.commit()
    await session.refresh(instance)
    return instance


async def delete_instance(
    session: AsyncSession,
    *,
    instance: Instance
) -> None:
    """
    Deletes Instance and its data (messages)
    """
    await session.delete(instance)
    await session.commit()
