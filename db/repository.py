"""
db.repository：只读 SQL 执行与元数据查询仓储。

职责：为 analyzer 提供 EXPLAIN 与 information_schema 类查询，不承载业务规则。
"""

from __future__ import annotations

import asyncio
import uuid

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

    async def run_oracle_explain_plan(
        self,
        sql_body: str,
        *,
        timeout_seconds: float,
    ) -> list[dict[str, object]]:
        """
        EXPLAIN PLAN 写入 plan_table 后查询行并清理；与 analyze 无关（Oracle 运行时统计另需 DBMS_XPLAN）。
        """
        stmt_id = ("SD" + uuid.uuid4().hex.replace("-", ""))[:30]

        async def _run() -> list[dict[str, object]]:
            await self._session.execute(
                text(f"EXPLAIN PLAN SET STATEMENT_ID = '{stmt_id}' FOR {sql_body}")
            )
            result = await self._session.execute(
                text(
                    """
                    SELECT id, operation, options, object_name, object_type,
                           cardinality, other
                    FROM plan_table
                    WHERE statement_id = :sid
                    ORDER BY id
                    """
                ),
                {"sid": stmt_id},
            )
            rows = [dict(m) for m in result.mappings().all()]
            await self._session.execute(
                text("DELETE FROM plan_table WHERE statement_id = :sid"),
                {"sid": stmt_id},
            )
            await self._session.commit()
            return rows

        try:
            return await asyncio.wait_for(_run(), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            try:
                await self._session.rollback()
            except Exception:
                pass
            raise DatabaseError(
                "Oracle EXPLAIN PLAN 超时",
                details={"timeout_seconds": timeout_seconds},
            ) from exc
        except Exception as exc:
            try:
                await self._session.rollback()
            except Exception:
                pass
            raise DatabaseError(
                "Oracle EXPLAIN PLAN 失败",
                details={"reason": str(exc), "statement_id": stmt_id},
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
            raise DatabaseError(
                "Oracle EXPLAIN 请使用 run_explain 专用路径（plan_table）",
                details={},
            )
        raise DatabaseError("未知方言", details={"dialect": dialect.value})

    async def run_explain(
        self,
        dialect: SqlDialect,
        sql: str,
        *,
        analyze: bool,
        timeout_seconds: float,
    ) -> list[dict[str, object]]:
        if dialect == SqlDialect.ORACLE:
            _ = analyze
            body = sql.strip().rstrip(";")
            return await self._repo.run_oracle_explain_plan(
                body,
                timeout_seconds=timeout_seconds,
            )
        explain_sql = self.build_explain_sql(dialect, sql, analyze)
        return await self._repo.fetch_all(
            explain_sql,
            timeout_seconds=timeout_seconds,
        )
