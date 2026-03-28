"""
db.redis_cache：异步 Redis 客户端封装与缓存键约定。

职责：对解析结果、计划 JSON 等提供可选缓存；无 Redis 时使用内存字典降级。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from app_exception import CacheError

try:
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover
    Redis = None  # type: ignore[misc, assignment]


class AsyncCache(ABC):
    """异步缓存抽象：统一 get/set/delete。"""

    @abstractmethod
    async def get_json(self, key: str) -> dict[str, Any] | None:
        """读取 JSON 对象；无键时返回 None。"""

    @abstractmethod
    async def set_json(
        self,
        key: str,
        value: dict[str, Any],
        *,
        ttl_seconds: int | None,
    ) -> None:
        """写入 JSON 对象，可选 TTL。"""

    @abstractmethod
    async def close(self) -> None:
        """释放连接或清空资源。"""


class InMemoryAsyncCache(AsyncCache):
    """开发环境降级用内存缓存。"""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}

    async def get_json(self, key: str) -> dict[str, Any] | None:
        return self._store.get(key)

    async def set_json(
        self,
        key: str,
        value: dict[str, Any],
        *,
        ttl_seconds: int | None,
    ) -> None:
        _ = ttl_seconds
        self._store[key] = value

    async def close(self) -> None:
        self._store.clear()


class RedisAsyncCache(AsyncCache):
    """基于 redis-py asyncio 的实现。"""

    def __init__(self, client: "Redis") -> None:
        self._client = client

    async def get_json(self, key: str) -> dict[str, Any] | None:
        try:
            raw = await self._client.get(key)
        except Exception as exc:
            raise CacheError("Redis GET 失败", details={"key": key, "reason": str(exc)}) from exc
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CacheError("缓存 JSON 解码失败", details={"key": key}) from exc

    async def set_json(
        self,
        key: str,
        value: dict[str, Any],
        *,
        ttl_seconds: int | None,
    ) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        try:
            if ttl_seconds is not None:
                await self._client.setex(key, ttl_seconds, payload)
            else:
                await self._client.set(key, payload)
        except Exception as exc:
            raise CacheError("Redis SET 失败", details={"key": key, "reason": str(exc)}) from exc

    async def close(self) -> None:
        try:
            aclose = getattr(self._client, "aclose", None)
            if callable(aclose):
                await aclose()
            else:
                close = getattr(self._client, "close", None)
                if callable(close):
                    await close()
        except Exception as exc:
            raise CacheError("Redis 关闭失败", details={"reason": str(exc)}) from exc


async def create_cache(redis_url: str | None) -> AsyncCache:
    if not redis_url or Redis is None:
        return InMemoryAsyncCache()
    try:
        client = Redis.from_url(redis_url, decode_responses=True)
    except Exception as exc:
        raise CacheError("无法连接 Redis", details={"reason": str(exc)}) from exc
    return RedisAsyncCache(client)
