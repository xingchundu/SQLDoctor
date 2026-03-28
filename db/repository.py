"""
db.repository：只读 SQL 执行与元数据查询仓储。

职责：为 analyzer 提供 EXPLAIN 与 information_schema 类查询，不承载业务规则。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app_exception import DatabaseError
from db.config import SqlDialect


class ReadOnlySqlRepository:
    """异步只读仓储：执行传入 SQL 并返回行列表。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def fetch_all(
        self,
        sql: str,
        *,
        params: dict[str, object] | None = None,
        timeout_seconds: float | None = None,
    ) -> list[dict[str, object]]:
        async def _run() -> list[dict[str, object]]:
            result = await self._session.execute(text(sql), params or {})
            rows = result.mappings().all()
            return [dict(r) for r in rows]

        try:
            if timeout_seconds is not None:
                return await asyncio.wait_for(_run(), timeout=timeout_seconds)
            return await _run()
        except asyncio.TimeoutError as exc:
            raise DatabaseError(
                "查询执行超时",
                details={"sql_prefix": sql[:200]},
            ) from exc
        except Exception as exc:
            raise DatabaseError(
                "查询执行失败",
                details={"sql_prefix": sql[:200], "reason": str(exc)},
            ) from exc


class ExplainRepository:
    """按方言构造 EXPLAIN 语句并执行。"""

    def __init__(self, repo: ReadOnlySqlRepository) -> None:
        self._repo = repo

    def build_explain_sql(self, dialect: SqlDialect, sql: str, analyze: bool) -> str:
        body = sql.strip().rstrip(";")
        if dialect == SqlDialect.MYSQL:
            prefix = "EXPLAIN ANALYZE " if analyze else "EXPLAIN "
            return prefix + body
        if dialect == SqlDialect.POSTGRES:
            if analyze:
                return f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {body}"
            return f"EXPLAIN (FORMAT JSON) {body}"
        if dialect == SqlDialect.ORACLE:
            if analyze:
                raise DatabaseError(
                    "Oracle 方言下 analyze=True 需 DBMS_XPLAN 等扩展，此处未实现",
                    details={},
                )
            return f"EXPLAIN PLAN FOR {body}"
        raise DatabaseError("未知方言", details={"dialect": dialect.value})

    async def run_explain(
        self,
        dialect: SqlDialect,
        sql: str,
        *,
        analyze: bool,
        timeout_seconds: float,
    ) -> list[dict[str, object]]:
        explain_sql = self.build_explain_sql(dialect, sql, analyze)
        return await self._repo.fetch_all(
            explain_sql,
            timeout_seconds=timeout_seconds,
        )
