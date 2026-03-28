"""
optimizer.models：建议与改写相关的 Pydantic v2 模型。

职责：为 API 与 agent 工具输出提供稳定 schema。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from analyzer.models import ParseResult, UnifiedPlan
from db.config import SqlDialect


class OptimizationSuggestion(BaseModel):
    """单条优化建议。"""

    id: str
    title: str
    detail: str
    severity: str = Field(description="low | medium | high")
    rationale: str | None = None


class SuggestionReport(BaseModel):
    """一次分析的建议集合。"""

    dialect: SqlDialect
    items: list[OptimizationSuggestion] = Field(default_factory=list)


class RewriteCandidate(BaseModel):
    """改写候选及说明。"""

    title: str
    sql_text: str
    notes: str | None = None


class RewriteReport(BaseModel):
    """改写层输出。"""

    dialect: SqlDialect
    originals: list[str] = Field(default_factory=list)
    candidates: list[RewriteCandidate] = Field(default_factory=list)


class OptimizerInputBundle(BaseModel):
    """优化器输入快照（便于测试与缓存键）。"""

    parse: ParseResult
    plan: UnifiedPlan | None = None
