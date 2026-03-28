"""
agent.tools：经 @tool 注册的 LangChain 工具，供 LangGraph ToolNode 调用。

职责：封装对 analyzer / optimizer 的异步调用；禁止裸 raise，统一抛出 AppException 子类。
"""

from __future__ import annotations

import json

from langchain_core.tools import tool

from agent.runtime import BUFFER_CTX, RUNTIME_CTX
from app_exception import AgentError
from db.config import SqlDialect


def _dialect(value: str) -> SqlDialect:
    try:
        return SqlDialect(value)
    except ValueError as exc:
        from app_exception import ConfigurationError

        raise ConfigurationError(
            "不支持的方言",
            details={"dialect": value, "allowed": [e.value for e in SqlDialect]},
        ) from exc


@tool
async def parse_sql(sql: str, dialect: str) -> str:
    """使用 sqlglot 解析 SQL 并返回 ParseResult JSON。"""
    deps = RUNTIME_CTX.get()
    buf = BUFFER_CTX.get()
    if deps is None or buf is None:
        raise AgentError("工具运行时未绑定", details={"tool": "parse_sql"})
    d = _dialect(dialect)
    result = await deps.parse_analyzer.analyze(sql, d)
    buf.parse = result
    return json.dumps(result.model_dump(), ensure_ascii=False)


@tool
async def fetch_execution_plan(sql: str, dialect: str, analyze: bool = True) -> str:
    """获取 EXPLAIN / EXPLAIN ANALYZE 结构化结果 JSON；无数据库时返回 skipped。"""
    deps = RUNTIME_CTX.get()
    buf = BUFFER_CTX.get()
    if deps is None or buf is None:
        raise AgentError("工具运行时未绑定", details={"tool": "fetch_execution_plan"})
    d = _dialect(dialect)
    if deps.plan_analyzer is None:
        payload = {"skipped": True, "reason": "未配置数据库连接"}
        return json.dumps(payload, ensure_ascii=False)
    plan = await deps.plan_analyzer.fetch_unified_plan(
        d,
        sql,
        analyze=analyze,
        timeout_seconds=deps.explain_timeout_seconds,
    )
    buf.plan = plan
    return json.dumps(plan.model_dump(), ensure_ascii=False)


@tool
async def generate_suggestions() -> str:
    """基于缓冲区中的解析与计划结果生成优化建议报告 JSON。"""
    deps = RUNTIME_CTX.get()
    buf = BUFFER_CTX.get()
    if deps is None or buf is None:
        raise AgentError("工具运行时未绑定", details={"tool": "generate_suggestions"})
    if buf.parse is None:
        raise AgentError("缺少解析结果", details={"hint": "先调用 parse_sql"})
    report = await deps.suggestion_service.build_report(
        buf.parse.dialect,
        buf.parse,
        buf.plan,
    )
    return json.dumps(report.model_dump(), ensure_ascii=False)


@tool
async def rewrite_sql(sql: str, dialect: str) -> str:
    """生成保守改写候选并返回 RewriteReport JSON。"""
    deps = RUNTIME_CTX.get()
    if deps is None:
        raise AgentError("工具运行时未绑定", details={"tool": "rewrite_sql"})
    d = _dialect(dialect)
    report = await deps.rewriter.build_report(sql, d)
    return json.dumps(report.model_dump(), ensure_ascii=False)


def all_sql_doctor_tools() -> list:
    """注册到 ToolNode 的工具列表（顺序无关）。"""
    return [parse_sql, fetch_execution_plan, generate_suggestions, rewrite_sql]
