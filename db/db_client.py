"""
db.db_client：多数据库异步客户端，执行 EXPLAIN 并返回统一结构的执行计划（dict）。

职责：按 URL/方言连接 MySQL、PostgreSQL、Oracle；将各库 EXPLAIN 结果映射为
      steps[].{type,key,rows,extra}；异常统一为 AppException 子类。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app_exception import AppException, ConfigurationError, DatabaseError
from db.config import SqlDialect


def _infer_dialect_from_url(database_url: str) -> SqlDialect:
    u = database_url.strip().lower()
    if "+oracledb" in u or "+cx_oracle" in u or u.startswith("oracle"):
        return SqlDialect.ORACLE
    if "mysql" in u or "mariadb" in u:
        return SqlDialect.MYSQL
    if "postgresql" in u or "postgres" in u:
        return SqlDialect.POSTGRES
    raise ConfigurationError(
        "无法从 database_url 推断方言，请显式传入 dialect",
        details={"url_prefix": database_url.split(":", 1)[0]},
    )


def _strip_sql_body(sql: str) -> str:
    return sql.strip().rstrip(";")


def _coerce_num(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _row_lower_keys(row: dict[str, Any]) -> dict[str, Any]:
    return {str(k).lower(): v for k, v in row.items()}


def _step(type_: Any, key: Any, rows: Any, extra: Any) -> dict[str, Any]:
    return {
        "type": None if type_ is None else str(type_),
        "key": None if key is None else str(key),
        "rows": _coerce_num(rows),
        "extra": None if extra is None else str(extra),
    }


def _mysql_rows_to_steps(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for row in rows:
        r = _row_lower_keys(row)
        extra = r.get("extra")
        if extra is None and "filtered" in r:
            extra = f"filtered={r.get('filtered')}"
        typ = r.get("select_type")
        access = r.get("type")
        type_str = " | ".join(x for x in (typ, access) if x)
        steps.append(
            _step(
                type_str or access,
                r.get("key"),
                r.get("rows"),
                extra,
            )
        )
    return steps


def _pg_collect_node(node: dict[str, Any], out: list[dict[str, Any]]) -> None:
    node_type = node.get("Node Type")
    key = node.get("Index Name") or node.get("Relation Name")
    rows = node.get("Actual Rows")
    if rows is None:
        rows = node.get("Plan Rows")
    extra_parts: list[str] = []
    if node.get("Join Type"):
        extra_parts.append(f"Join={node['Join Type']}")
    if node.get("Filter"):
        extra_parts.append(f"Filter={node['Filter']}")
    if node.get("Hash Cond"):
        extra_parts.append(f"HashCond={node['Hash Cond']}")
    if node.get("Merge Cond"):
        extra_parts.append(f"MergeCond={node['Merge Cond']}")
    extra = "; ".join(extra_parts) if extra_parts else None
    out.append(_step(node_type, key, rows, extra))
    for child in node.get("Plans") or []:
        if isinstance(child, dict):
            _pg_collect_node(child, out)


def _pg_json_to_steps(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise DatabaseError(
                "PostgreSQL EXPLAIN JSON 解析失败",
                details={"reason": str(exc)},
            ) from exc
    if isinstance(payload, list) and payload:
        payload = payload[0]
    if not isinstance(payload, dict):
        raise DatabaseError(
            "PostgreSQL EXPLAIN 返回非预期结构",
            details={"payload_type": type(payload).__name__},
        )
    root = payload.get("Plan")
    if not isinstance(root, dict):
        raise DatabaseError(
            "PostgreSQL EXPLAIN JSON 缺少 Plan 根节点",
            details={},
        )
    out: list[dict[str, Any]] = []
    _pg_collect_node(root, out)
    return out


def _oracle_rows_to_steps(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for row in rows:
        r = _row_lower_keys(row)
        op = r.get("operation") or ""
        opt = r.get("options") or ""
        type_str = f"{op} {opt}".strip() or None
        extra_parts = [x for x in (r.get("other"), r.get("object_type")) if x]
        extra = "; ".join(str(x) for x in extra_parts) if extra_parts else None
        steps.append(
            _step(
                type_str,
                r.get("object_name"),
                r.get("cardinality"),
                extra,
            )
        )
    return steps


class ExplainDbClient:
    """
    异步 EXPLAIN 客户端：内部持有 AsyncEngine，按方言执行 EXPLAIN 并返回结构化 dict。

    统一输出：
        {
            "dialect": str,
            "analyze": bool,
            "steps": [ {"type","key","rows","extra"}, ... ],
            "statement_id": str | None,  # Oracle 清理用
        }
    """

    def __init__(
        self,
        database_url: str,
        *,
        dialect: SqlDialect | None = None,
    ) -> None:
        if not database_url or not database_url.strip():
            raise ConfigurationError("database_url 不能为空", details={})
        self._database_url = database_url.strip()
        self._dialect = dialect or _infer_dialect_from_url(self._database_url)
        self._engine: AsyncEngine | None = None

    @property
    def dialect(self) -> SqlDialect:
        return self._dialect

    async def _get_engine(self) -> AsyncEngine:
        if self._engine is None:
            try:
                self._engine = create_async_engine(
                    self._database_url,
                    pool_pre_ping=True,
                    echo=False,
                )
            except Exception as exc:
                raise DatabaseError(
                    "创建数据库引擎失败",
                    details={
                        "url_prefix": self._database_url.split(":", 1)[0],
                        "reason": str(exc),
                    },
                ) from exc
        return self._engine

    def _session_factory(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    async def explain(
        self,
        sql: str,
        *,
        analyze: bool = False,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """
        执行 EXPLAIN（或方言下的 ANALYZE 变体），返回包含 steps 的 dict。

        每步均含：type, key, rows, extra（无则 null）。
        """
        if not sql or not sql.strip():
            raise DatabaseError("SQL 为空", details={})

        async def _run() -> dict[str, Any]:
            engine = await self._get_engine()
            factory = self._session_factory(engine)
            async with factory() as session:
                if self._dialect == SqlDialect.MYSQL:
                    return await self._explain_mysql(session, sql, analyze=analyze)
                if self._dialect == SqlDialect.POSTGRES:
                    return await self._explain_postgres(session, sql, analyze=analyze)
                if self._dialect == SqlDialect.ORACLE:
                    return await self._explain_oracle(session, sql, analyze=analyze)
                raise DatabaseError(
                    "不支持的方言",
                    details={"dialect": self._dialect.value},
                )

        try:
            return await asyncio.wait_for(_run(), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise DatabaseError(
                "EXPLAIN 执行超时",
                details={"timeout_seconds": timeout_seconds},
            ) from exc
        except AppException:
            raise
        except Exception as exc:
            raise DatabaseError(
                "EXPLAIN 执行失败",
                details={"reason": str(exc)},
            ) from exc

    async def _explain_mysql(
        self,
        session: AsyncSession,
        sql: str,
        *,
        analyze: bool,
    ) -> dict[str, Any]:
        body = _strip_sql_body(sql)
        prefix = "EXPLAIN ANALYZE " if analyze else "EXPLAIN "
        explain_sql = prefix + body
        try:
            result = await session.execute(text(explain_sql))
            rows = [dict(m) for m in result.mappings().all()]
        except Exception as exc:
            raise DatabaseError(
                "MySQL EXPLAIN 失败",
                details={"reason": str(exc)},
            ) from exc
        steps = _mysql_rows_to_steps(rows)
        return {
            "dialect": SqlDialect.MYSQL.value,
            "analyze": analyze,
            "steps": steps,
            "statement_id": None,
        }

    async def _explain_postgres(
        self,
        session: AsyncSession,
        sql: str,
        *,
        analyze: bool,
    ) -> dict[str, Any]:
        body = _strip_sql_body(sql)
        if analyze:
            explain_sql = f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {body}"
        else:
            explain_sql = f"EXPLAIN (FORMAT JSON) {body}"
        try:
            result = await session.execute(text(explain_sql))
            rows = [dict(m) for m in result.mappings().all()]
        except Exception as exc:
            raise DatabaseError(
                "PostgreSQL EXPLAIN 失败",
                details={"reason": str(exc)},
            ) from exc
        if not rows:
            raise DatabaseError("PostgreSQL EXPLAIN 无返回行", details={})
        r0 = _row_lower_keys(rows[0])
        raw = r0.get("query plan")
        if raw is None:
            raw = r0.get("QUERY PLAN")
        steps = _pg_json_to_steps(raw)
        return {
            "dialect": SqlDialect.POSTGRES.value,
            "analyze": analyze,
            "steps": steps,
            "statement_id": None,
        }

    async def _explain_oracle(
        self,
        session: AsyncSession,
        sql: str,
        *,
        analyze: bool,
    ) -> dict[str, Any]:
        _ = analyze
        body = _strip_sql_body(sql)
        stmt_id = ("SD" + uuid.uuid4().hex.replace("-", ""))[:30]
        explain_sql = f"EXPLAIN PLAN SET STATEMENT_ID = '{stmt_id}' FOR {body}"
        try:
            await session.execute(text(explain_sql))
            q = text(
                """
                SELECT id, operation, options, object_name, object_type,
                       cardinality, other
                FROM plan_table
                WHERE statement_id = :sid
                ORDER BY id
                """
            )
            result = await session.execute(q, {"sid": stmt_id})
            rows = [dict(m) for m in result.mappings().all()]
        except Exception as exc:
            raise DatabaseError(
                "Oracle EXPLAIN PLAN 失败",
                details={"reason": str(exc), "statement_id": stmt_id},
            ) from exc
        finally:
            try:
                await session.execute(
                    text("DELETE FROM plan_table WHERE statement_id = :sid"),
                    {"sid": stmt_id},
                )
                await session.commit()
            except Exception:
                await session.rollback()

        steps = _oracle_rows_to_steps(rows)
        return {
            "dialect": SqlDialect.ORACLE.value,
            "analyze": False,
            "steps": steps,
            "statement_id": stmt_id,
        }
