"""
analyzer.plan_fetcher：调用仓储获取 EXPLAIN 并映射为 UnifiedPlan。

职责：屏蔽各数据库 EXPLAIN 差异的入口；复杂映射可后续按方言拆分策略类。
"""

from __future__ import annotations

import json
from typing import Any

from analyzer.models import PlanNode, UnifiedPlan
from app_exception import PlanAnalysisError
from db.config import SqlDialect
from db.repository import ExplainRepository


def _rows_to_jsonish(rows: list[dict[str, Any]]) -> Any:
    if not rows:
        return []
    if len(rows) == 1 and "QUERY PLAN" in rows[0]:
        return rows[0]["QUERY PLAN"]
    if len(rows) == 1 and "EXPLAIN PLAN" in rows[0]:
        return rows[0]
    return rows


def _build_stub_tree(dialect: SqlDialect, payload: Any) -> PlanNode:
    raw_preview = json.dumps(payload, ensure_ascii=False)[:2000]
    return PlanNode(
        id="root",
        op="EXPLAIN",
        detail=f"{dialect.value} 原始计划摘要",
        children=[],
        raw={"preview": raw_preview},
    )


class ExecutionPlanAnalyzer:
    """异步获取并结构化执行计划。"""

    def __init__(self, explain_repo: ExplainRepository) -> None:
        self._explain = explain_repo

    async def fetch_unified_plan(
        self,
        dialect: SqlDialect,
        sql: str,
        *,
        analyze: bool,
        timeout_seconds: float,
    ) -> UnifiedPlan:
        try:
            rows = await self._explain.run_explain(
                dialect,
                sql,
                analyze=analyze,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            raise PlanAnalysisError(
                "获取执行计划失败",
                details={"dialect": dialect.value, "reason": str(exc)},
            ) from exc
        payload = _rows_to_jsonish(rows)
        tree = _build_stub_tree(dialect, payload)
        analyzed_flag = analyze if dialect != SqlDialect.ORACLE else False
        return UnifiedPlan(
            dialect=dialect,
            analyzed=analyzed_flag,
            raw_rows=rows,
            tree=tree,
        )
