"""
backend.services.runtime_factory：为单次 HTTP 请求构建 ToolRuntimeDeps。

职责：在有/无数据库会话两种模式下装配 plan_analyzer，并保持类型一致。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from agent.runtime import ToolRuntimeDeps
from analyzer.parser import SqlParseAnalyzer
from analyzer.plan_fetcher import ExecutionPlanAnalyzer
from backend.config import Settings
from db.repository import ExplainRepository, ReadOnlySqlRepository
from optimizer.rewriter import SqlRewriteService
from optimizer.suggestions import OptimizationSuggestionService


class ToolRuntimeFactory:
    """根据可选 AsyncSession 创建 LangGraph 工具运行时依赖。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def build(self, session: AsyncSession | None) -> ToolRuntimeDeps:
        parse = SqlParseAnalyzer()
        suggestions = OptimizationSuggestionService()
        rewriter = SqlRewriteService()
        plan_analyzer: ExecutionPlanAnalyzer | None = None
        if session is not None:
            read_repo = ReadOnlySqlRepository(session)
            explain = ExplainRepository(read_repo)
            plan_analyzer = ExecutionPlanAnalyzer(explain)
        return ToolRuntimeDeps(
            parse_analyzer=parse,
            plan_analyzer=plan_analyzer,
            suggestion_service=suggestions,
            rewriter=rewriter,
            explain_timeout_seconds=self._settings.explain_timeout_seconds,
        )
