from app.utils.config import settings
from shared.database import make_async_engine

engine, async_session_maker = make_async_engine(
    settings.database_url,
    pool_size=settings.pool_size,
    max_overflow=settings.max_overflow,
)