"""PostgreSQL setup and short transactions."""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base


def connect_database(url):
    """Create the async engine and transaction factory."""
    engine = create_async_engine(url, pool_pre_ping=True)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def create_tables(engine):
    """Create missing tables for this fresh-install MVP."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
