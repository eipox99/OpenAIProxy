from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from openproxy.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    poolclass=NullPool,  # Required for SQLite with event listeners
)


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record):
    """Configure SQLite for safety and concurrency."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.execute("PRAGMA busy_timeout = 5000")
    cursor.close()


async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency that yields an async database session."""
    async with async_session_factory() as session:
        yield session
