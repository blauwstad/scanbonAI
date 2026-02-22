"""
Async SQLAlchemy engine and session factory for ScanbonAI.

Usage
-----
In FastAPI route handlers use the ``get_db_session`` dependency from
``app.dependencies``.  For standalone scripts (migrations, CLI tools) use
the ``async_session`` factory directly inside an ``async with`` block.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
# pool_pre_ping keeps connections alive across network interruptions.
# echo=False in production; override via SQLALCHEMY_ECHO env var if needed.
_engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=False,
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=_engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


# ---------------------------------------------------------------------------
# Lifecycle helpers (called from app startup/shutdown events)
# ---------------------------------------------------------------------------
async def connect_db() -> None:
    """Verify the database connection is reachable at startup."""
    async with _engine.connect() as conn:
        await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
    logger.info("database.connected", url=_mask_url(settings.DATABASE_URL))


async def disconnect_db() -> None:
    """Dispose the connection pool gracefully on shutdown."""
    await _engine.dispose()
    logger.info("database.disconnected")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def _mask_url(url: str) -> str:
    """Return a loggable DSN with the password replaced by '***'."""
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        if parsed.password:
            netloc = parsed.netloc.replace(parsed.password, "***")
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:  # noqa: BLE001
        pass
    return "<redacted>"


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager that provides a database session with rollback on error."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
