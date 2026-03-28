"""
db.engine：创建与销毁异步 SQLAlchemy Engine。

职责：按 URL 懒创建引擎，供会话工厂复用；所有创建均为 async 兼容配置。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app_exception import ConfigurationError, DatabaseError


class AsyncEngineFactory:
    """根据 database_url 构建单例风格 AsyncEngine。"""

    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None
        self._url: str | None = None

    def get_engine(self, database_url: str | None) -> AsyncEngine | None:
        if not database_url:
            return None
        if self._engine is not None and self._url == database_url:
            return self._engine
        self._dispose_sync()
        self._url = database_url
        try:
            self._engine = create_async_engine(
                database_url,
                pool_pre_ping=True,
                echo=False,
            )
        except Exception as exc:
            raise DatabaseError(
                "无法创建异步数据库引擎",
                details={"url_prefix": database_url.split(":", 1)[0], "reason": str(exc)},
            ) from exc
        return self._engine

    def require_engine(self, database_url: str | None) -> AsyncEngine:
        engine = self.get_engine(database_url)
        if engine is None:
            raise ConfigurationError(
                "未配置 database_url，无法访问数据库",
                details={},
            )
        return engine

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._url = None

    def _dispose_sync(self) -> None:
        self._engine = None
        self._url = None


engine_factory = AsyncEngineFactory()
