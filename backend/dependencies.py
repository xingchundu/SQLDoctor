"""
backend.dependencies：FastAPI Depends 异步工厂（数据库会话、缓存、分析流水线）。

职责：集中装配跨请求资源，保持路由层薄。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import Request

from backend.config import Settings
from db.engine import engine_factory
from db.redis_cache import AsyncCache, InMemoryAsyncCache, create_cache
from db.session import build_session_factory


@asynccontextmanager
async def lifespan_cache(settings: Settings) -> AsyncIterator[AsyncCache]:
    cache = await create_cache(settings.redis_url)
    try:
        yield cache
    finally:
        await cache.close()


def get_cache(request: Request) -> AsyncCache:
    """从 app.state 读取全局缓存（在 main 生命周期内挂载）。"""
    cache = getattr(request.app.state, "cache", None)
    if cache is None:
        return InMemoryAsyncCache()
    return cache


@asynccontextmanager
async def optional_db_session(
    settings: Settings,
) -> AsyncIterator[AsyncSession | None]:
    engine = engine_factory.get_engine(settings.database_url)
    if engine is None:
        yield None
        return
    factory = build_session_factory(engine)
    async with factory.session() as session:
        yield session
