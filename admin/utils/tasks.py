from datetime import datetime

from sqlalchemy import delete

from admin.utils.db import async_session_maker
from shared.models import DBSession


async def purge_expired_sessions():
    async with async_session_maker() as db:
        await db.execute(delete(DBSession).where(DBSession.expires_at < datetime.utcnow()))
        await db.commit()
