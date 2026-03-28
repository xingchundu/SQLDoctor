"""
analyzer.models：解析与执行计划相关的 Pydantic v2 模型。

职责：定义跨层传输的 DTO，避免在 API 与图状态中散落弱类型 dict。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from db.config import SqlDialect


class ParseIssue(BaseModel):
    """静态分析发现的单条问题。"""

    severity: str = Field(description="info | warning | error")
    code: str
    message: str
    line_hint: int | None = None


class ParseResult(BaseModel):
    """sqlglot 解析与轻量静态分析结果。"""

    dialect: SqlDialect
    normalized_sql: str | None = None
    ast_kind: str | None = Field(default=None, description="根节点类型名")
    issues: list[ParseIssue] = Field(default_factory=list)
    tables_referenced: list[str] = Field(default_factory=list)


class PlanNode(BaseModel):
    """统一执行计划树节点（各库映射后的最小公共字段）。"""

    id: str
    op: str = Field(description="算子或操作描述")
    detail: str | None = None
    children: list[PlanNode] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class UnifiedPlan(BaseModel):
    """与方言无关的执行计划视图。"""

    dialect: SqlDialect
    analyzed: bool
    raw_rows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="数据库返回的原始行，便于前端调试",
    )
    tree: PlanNode | None = None


class AnalyzerBundle(BaseModel):
    """一次分析流水线在 analyzer 侧的聚合输出。"""

    parse: ParseResult
    plan: UnifiedPlan | None = None
