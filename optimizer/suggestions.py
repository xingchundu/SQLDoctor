"""
optimizer.suggestions：基于解析与计划特征的规则化建议生成。

职责：产出 SuggestionReport；后续可接入 LLM 对 items 做重排或补充说明。
"""

from __future__ import annotations

import uuid

from analyzer.models import ParseResult, UnifiedPlan
from app_exception import OptimizerError
from db.config import SqlDialect
from optimizer.models import OptimizationSuggestion, SuggestionReport


class OptimizationSuggestionService:
    """构建优化建议报告的领域服务。"""

    async def build_report(
        self,
        dialect: SqlDialect,
        parse: ParseResult,
        plan: UnifiedPlan | None,
    ) -> SuggestionReport:
        try:
            items = self._from_parse(parse)
            items.extend(self._from_plan(dialect, plan))
            return SuggestionReport(dialect=dialect, items=items)
        except OptimizerError:
            raise
        except Exception as exc:
            raise OptimizerError(
                "生成优化建议失败",
                details={"reason": str(exc)},
            ) from exc

    def _from_parse(self, parse: ParseResult) -> list[OptimizationSuggestion]:
        items: list[OptimizationSuggestion] = []
        for issue in parse.issues:
            items.append(
                OptimizationSuggestion(
                    id=str(uuid.uuid4()),
                    title=f"静态分析：{issue.code}",
                    detail=issue.message,
                    severity="medium" if issue.severity == "warning" else "low",
                    rationale="来自 sqlglot 结构遍历",
                )
            )
        if len(parse.tables_referenced) > 6:
            items.append(
                OptimizationSuggestion(
                    id=str(uuid.uuid4()),
                    title="多表引用",
                    detail=f"引用表数量较多（{len(parse.tables_referenced)}），请关注 JOIN 顺序与索引覆盖",
                    severity="low",
                    rationale="基于 AST 表收集",
                )
            )
        return items

    def _from_plan(
        self,
        dialect: SqlDialect,
        plan: UnifiedPlan | None,
    ) -> list[OptimizationSuggestion]:
        _ = dialect
        if plan is None:
            return []
        return [
            OptimizationSuggestion(
                id=str(uuid.uuid4()),
                title="执行计划已采集",
                detail="已获取 EXPLAIN 输出，请结合可视化与原始行进一步诊断",
                severity="low",
                rationale="计划结构化占位实现",
            )
        ]
