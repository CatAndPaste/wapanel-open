from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker, AsyncEngine
from sqlalchemy.orm import sessionmaker, declarative_base


def make_async_engine(url: str, echo: bool = False, **kwargs) \
        -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """
    Builds asyncpg engine and session maker
    """
    engine = create_async_engine(url, echo=echo, **kwargs)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_maker


Base = declarative_base()
