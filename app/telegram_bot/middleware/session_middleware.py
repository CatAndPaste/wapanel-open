from aiogram import BaseMiddleware

from app.utils.db import async_session_maker


class DBSessionMiddleware(BaseMiddleware):
    """
    Opens async connection to database, passes it as session argument to handler and commits changes on exit.
    """
    async def __call__(self, handler, event, data):
        async with async_session_maker() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise