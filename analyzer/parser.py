"""
analyzer.parser：使用 sqlglot 解析 SQL 并做轻量静态分析。

职责：生成 ParseResult；CPU 密集解析通过 asyncio.to_thread 避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError as SqlglotParseError

from analyzer.ast_advisor import SqlAstOptimizationAdvisor, hits_to_parse_issues
from analyzer.models import ParseIssue, ParseResult
from app_exception import ParseError
from db.config import SqlDialect


def _sqlglot_dialect(dialect: SqlDialect) -> str:
    return dialect.value


def _extract_tables(expression: exp.Expression) -> list[str]:
    tables: list[str] = []
    for t in expression.find_all(exp.Table):
        name = ".".join(x for x in (t.db, t.name) if x)
        if name:
            tables.append(name)
    return sorted(set(tables))


def _collect_issues(expression: exp.Expression) -> list[ParseIssue]:
    issues: list[ParseIssue] = []
    joins = list(expression.find_all(exp.Join))
    if len(joins) > 8:
        issues.append(
            ParseIssue(
                severity="warning",
                code="MANY_JOINS",
                message=f"检测到较多 JOIN（{len(joins)}），请关注计划与统计信息",
            )
        )
    # SELECT * 由 analyzer.ast_advisor.SqlAstOptimizationAdvisor（NO_SELECT_STAR）统一给出
    return issues


def _parse_sync(sql: str, dialect: SqlDialect) -> ParseResult:
    try:
        parsed = sqlglot.parse_one(sql, read=_sqlglot_dialect(dialect))
    except SqlglotParseError as exc:
        raise ParseError("sqlglot 解析失败", details={"reason": str(exc)}) from exc
    tables = _extract_tables(parsed)
    issues = _collect_issues(parsed)
    issues.extend(
        hits_to_parse_issues(
            SqlAstOptimizationAdvisor().analyze_tree(parsed),
        )
    )
    normalized = parsed.sql(dialect=_sqlglot_dialect(dialect))
    return ParseResult(
        dialect=dialect,
        normalized_sql=normalized,
        ast_kind=type(parsed).__name__,
        issues=issues,
        tables_referenced=tables,
    )


class SqlParseAnalyzer:
    """对外暴露异步解析接口的薄封装。"""

    async def analyze(self, sql: str, dialect: SqlDialect) -> ParseResult:
        if not sql or not sql.strip():
            raise ParseError("SQL 为空", details={})
        return await asyncio.to_thread(_parse_sync, sql, dialect)

    async def analyze_batch(
        self,
        items: Iterable[tuple[str, SqlDialect]],
    ) -> list[ParseResult]:
        out: list[ParseResult] = []
        for text_sql, d in items:
            out.append(await self.analyze(text_sql, d))
        return out
