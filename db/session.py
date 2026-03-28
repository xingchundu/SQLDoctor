"""
db.session：异步会话工厂与会话上下文工具。

职责：为仓储层提供 scoped async_sessionmaker，不泄漏 Engine 实现细节。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app_exception import AppException, DatabaseError


class AsyncSessionFactory:
    """基于 AsyncEngine 构建会话工厂。"""

    def __init__(self, engine: AsyncEngine) -> None:
        self._maker = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._maker() as s:
            try:
                yield s
            except AppException:
                raise
            except Exception as exc:
                raise DatabaseError(
                    "数据库会话执行失败",
                    details={"reason": str(exc)},
                ) from exc


def build_session_factory(engine: AsyncEngine) -> AsyncSessionFactory:
    return AsyncSessionFactory(engine)
