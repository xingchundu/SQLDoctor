"""
optimizer.rewriter：基于 sqlglot 的安全改写与候选生成。

职责：输出 RewriteReport；默认仅做保守格式化，避免未经验证的语义变换。
"""

from __future__ import annotations

import asyncio

import sqlglot
from sqlglot.errors import ParseError as SqlglotParseError

from app_exception import OptimizerError, ParseError
from db.config import SqlDialect
from optimizer.models import RewriteCandidate, RewriteReport


def _rewrite_sync(sql: str, dialect: SqlDialect) -> str:
    try:
        parsed = sqlglot.parse_one(sql, read=dialect.value)
        return parsed.sql(dialect=dialect.value, pretty=True)
    except SqlglotParseError as exc:
        raise ParseError("改写前解析失败", details={"reason": str(exc)}) from exc


class SqlRewriteService:
    """异步 SQL 改写服务。"""

    async def build_report(self, sql: str, dialect: SqlDialect) -> RewriteReport:
        try:
            formatted = await asyncio.to_thread(_rewrite_sync, sql, dialect)
            candidate = RewriteCandidate(
                title="格式化 / pretty 打印",
                sql_text=formatted,
                notes="保守改写：仅重排格式，不改变语义",
            )
            return RewriteReport(
                dialect=dialect,
                originals=[sql],
                candidates=[candidate],
            )
        except ParseError:
            raise
        except Exception as exc:
            raise OptimizerError(
                "SQL 改写失败",
                details={"reason": str(exc)},
            ) from exc
