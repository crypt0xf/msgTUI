"""Async SQLAlchemy engine + session factory."""
from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from server.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    cfg = get_settings()
    return create_async_engine(
        cfg.db_url,
        echo=False,
        connect_args={"check_same_thread": False} if "sqlite" in cfg.db_url else {},
    )


engine = _make_engine()
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    from server import models  # noqa: F401 – ensures models are registered
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
