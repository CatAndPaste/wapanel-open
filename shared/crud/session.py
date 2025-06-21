from typing import Optional, List, Any
from datetime import datetime, timedelta
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.models import DBSession


async def list_sessions(
    session: AsyncSession,
    *,
    user_id: Optional[int] = None,
    offset: int = 0,
    limit: int = 100
) -> List[DBSession]:
    """
    Sessions list, pagination supported
    """
    q = select(DBSession)
    if user_id is not None:
        q = q.where(DBSession.user_id == user_id)
    q = q.offset(offset).limit(limit)
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_session_by_hash(
    session: AsyncSession,
    *,
    token_hash: str
) -> Optional[DBSession]:
    """
    Get session by hash if present, else - None
    """
    q = select(DBSession).options(selectinload(DBSession.user)).where(DBSession.token_hash == token_hash)
    result = await session.execute(q)
    return result.scalars().first()


async def create_session(
    session: AsyncSession,
    *,
    user_id: int,
    token_hash: str,
    csrf_token: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> DBSession:
    """
    Create new session
    """
    dbs = DBSession(
        user_id=user_id,
        token_hash=token_hash,
        csrf_token=csrf_token,
        ip=ip,
        user_agent=user_agent
    )
    session.add(dbs)
    await session.commit()
    await session.refresh(dbs)
    return dbs


async def update_session(
    session: AsyncSession,
    dbs: DBSession,
    **fields: Any
) -> DBSession:
    """
    Update session
    """
    for field, value in fields.items():
        if hasattr(dbs, field):
            setattr(dbs, field, value)
    session.add(dbs)
    await session.commit()
    await session.refresh(dbs)
    return dbs


async def delete_session(
    session: AsyncSession,
    *,
    dbs: DBSession
) -> None:
    """
    Invalidate (delete) session
    """
    await session.delete(dbs)
    await session.commit()


async def delete_sessions_for_user(
    session: AsyncSession,
    *,
    user_id: int
) -> int:
    """
    Invalidate user sessions, deletes them all
    Returns number deleted
    """
    q = delete(DBSession).where(DBSession.user_id == user_id).returning(DBSession.id)
    result = await session.execute(q)
    await session.commit()
    deleted = result.all()
    return len(deleted)


async def touch_session(session: AsyncSession, session_obj: DBSession) -> None:
    """
    Updates last_seen -> session is valid for next 14 days
    """
    session_obj.last_seen = datetime.utcnow()
    session.add(session_obj)
    await session.commit()
