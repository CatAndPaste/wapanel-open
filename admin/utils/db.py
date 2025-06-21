from shared.database import make_async_engine
from admin.utils.config import settings

engine, async_session_maker = make_async_engine(
    settings.database_url,
    pool_size=settings.pool_size,
    max_overflow=settings.max_overflow,
)


async def get_session():
    async with async_session_maker() as session:
        yield session
