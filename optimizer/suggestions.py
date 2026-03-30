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
            if plan is None:
                items.append(
                    OptimizationSuggestion(
                        id=str(uuid.uuid4()),
                        title="未采集执行计划（EXPLAIN）",
                        detail=(
                            "未提供可用数据库连接或未返回 EXPLAIN，无法根据 type/rows/key/Extra 做计划层建议。"
                            "可在界面填写异步连接串并通过「测试连接」后，在分析请求中携带 database_url；"
                            "或在服务器 .env 中配置 DATABASE_URL。"
                        ),
                        severity="low",
                        rationale="计划数据缺失",
                    )
                )
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
        from analyzer.plan_analyzer import ExecutionPlanAnalyzer as PlanRiskEngine

        items: list[OptimizationSuggestion] = []
        try:
            risk = PlanRiskEngine().analyze(plan.raw_rows or [])
            sev_for = {
                "FULL_TABLE_SCAN": "high",
                "ROWS_TOO_LARGE": "high",
                "USING_TEMPORARY": "high",
                "USING_FILESORT": "medium",
                "INDEX_NOT_USED": "medium",
            }
            for pb in risk.problems:
                steps = list(pb.affected_steps or [])
                step_txt = (
                    f"影响步骤：{', '.join(f'step{s}' for s in steps)}。"
                    if steps
                    else ""
                )
                items.append(
                    OptimizationSuggestion(
                        id=str(uuid.uuid4()),
                        title=f"[执行计划] {pb.title}",
                        detail=f"{step_txt}{pb.reason}".strip(),
                        severity=sev_for.get(pb.code, "medium"),
                        rationale=pb.code,
                    )
                )
        except Exception:
            items.append(
                OptimizationSuggestion(
                    id=str(uuid.uuid4()),
                    title="执行计划已采集",
                    detail="已获取 EXPLAIN，但计划规则引擎解析失败，请结合响应中的 plan.raw_rows 人工查看。",
                    severity="low",
                    rationale="plan_parse_fallback",
                )
            )
        if not items:
            items.append(
                OptimizationSuggestion(
                    id=str(uuid.uuid4()),
                    title="执行计划已采集",
                    detail="已获取 EXPLAIN，当前规则未识别明显风险模式；请结合原始计划行与业务负载复核。",
                    severity="low",
                    rationale="plan_no_hits",
                )
            )
        return items
