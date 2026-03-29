"""
backend.api.routes.db_test：按需测试数据库连接（不复用全局 Engine）。

职责：供前端在分析前校验用户输入的异步连接串是否可用。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app_exception import ConfigurationError
from db.config import SqlDialect
from db.session import build_session_factory

router = APIRouter()


class DbTestRequest(BaseModel):
    dialect: str = Field(description="mysql | postgres | oracle")
    database_url: str = Field(min_length=1, description="SQLAlchemy 异步 URL")


class DbTestResponse(BaseModel):
    ok: bool
    message: str


def _ping_sql(dialect: SqlDialect) -> str:
    if dialect == SqlDialect.ORACLE:
        return "SELECT 1 FROM DUAL"
    return "SELECT 1"


@router.post("/test-connection", response_model=DbTestResponse)
async def test_connection(body: DbTestRequest) -> DbTestResponse:
    try:
        dialect = SqlDialect(body.dialect)
    except ValueError as exc:
        raise ConfigurationError(
            "不支持的 dialect",
            details={"dialect": body.dialect, "allowed": [e.value for e in SqlDialect]},
        ) from exc

    url = body.database_url.strip()
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        factory = build_session_factory(engine)
        async with factory.session() as session:
            await asyncio.wait_for(
                session.execute(text(_ping_sql(dialect))),
                timeout=15.0,
            )
        return DbTestResponse(ok=True, message="连接成功，可输入 SQL 并获取执行计划进行分析。")
    except asyncio.TimeoutError:
        return DbTestResponse(ok=False, message="连接超时（15s），请检查地址、端口与网络。")
    except Exception as exc:
        return DbTestResponse(ok=False, message=str(exc))
    finally:
        await engine.dispose()
