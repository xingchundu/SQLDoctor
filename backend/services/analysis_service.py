"""
backend.services.analysis_service：触发 LangGraph 流水线并整理 HTTP 响应模型。

职责：隔离 FastAPI 与 agent.graph 的交互细节。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, Field

from analyzer.plan_analyzer import ExecutionPlanAnalyzer
from agent.graph import SqlDoctorAgent
from agent.runtime import ToolRuntimeDeps
from app_exception import AgentError


class AnalysisRequest(BaseModel):
    """分析接口入参。"""

    sql: str = Field(min_length=1)
    dialect: str = Field(default="mysql")
    database_url: str | None = Field(
        default=None,
        description="可选；与方言匹配的 SQLAlchemy 异步连接串，用于本次请求的 EXPLAIN",
    )
    suggestions_only: bool = Field(
        default=False,
        description="为 True 时响应仅含 dialect + items，不含 parse/plan/messages/rewrite",
    )


class SuggestionsOnlyResponse(BaseModel):
    """仅返回规则化优化建议（精简接口）。"""

    dialect: str | None = Field(default=None, description="分析所用方言")
    items: list[dict[str, Any]] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    """分析接口出参：原始消息轨迹 + 提取后的结构化片段。"""

    messages: list[dict[str, Any]] = Field(default_factory=list)
    parse: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
    plan_analysis: dict[str, Any] | None = Field(
        default=None,
        description="基于 EXPLAIN raw_rows 的规则分析（problems / risk_level / details）",
    )
    suggestions: dict[str, Any] | None = None
    rewrite: dict[str, Any] | None = None


class AnalysisApplicationService:
    """应用服务：运行 SQL Doctor 流水线。"""

    def __init__(self, agent: SqlDoctorAgent | None = None) -> None:
        self._agent = agent or SqlDoctorAgent()

    async def analyze(
        self,
        body: AnalysisRequest,
        *,
        runtime: ToolRuntimeDeps,
    ) -> AnalysisResponse:
        state = await self._agent.run_pipeline(
            body.sql,
            body.dialect,
            runtime=runtime,
        )
        return self._state_to_response(state)

    def _state_to_response(self, state: dict[str, Any]) -> AnalysisResponse:
        messages_out: list[dict[str, Any]] = []
        parse_data: dict[str, Any] | None = None
        plan_data: dict[str, Any] | None = None
        suggestions_data: dict[str, Any] | None = None
        rewrite_data: dict[str, Any] | None = None

        raw_messages = state.get("messages") or []
        for m in raw_messages:
            if isinstance(m, ToolMessage):
                messages_out.append(
                    {
                        "role": "tool",
                        "name": m.name,
                        "content": m.content,
                    }
                )
                payload = self._safe_json(m.content)
                if m.name == "parse_sql":
                    parse_data = payload
                elif m.name == "fetch_execution_plan":
                    plan_data = payload
                elif m.name == "generate_suggestions":
                    suggestions_data = payload
                elif m.name == "rewrite_sql":
                    rewrite_data = payload
            elif isinstance(m, AIMessage):
                messages_out.append(
                    {
                        "role": "assistant",
                        "content": m.content,
                        "tool_calls": m.tool_calls,
                    }
                )

        if not messages_out:
            raise AgentError("流水线未产生任何消息", details={})
        plan_analysis_data = self._plan_analysis_from_plan(plan_data)
        return AnalysisResponse(
            messages=messages_out,
            parse=parse_data,
            plan=plan_data,
            plan_analysis=plan_analysis_data,
            suggestions=suggestions_data,
            rewrite=rewrite_data,
        )

    def _plan_analysis_from_plan(
        self, plan_data: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if not plan_data or not isinstance(plan_data, dict):
            return None
        if plan_data.get("skipped"):
            return None
        rows = plan_data.get("raw_rows")
        if not isinstance(rows, list) or not rows:
            return None
        try:
            return ExecutionPlanAnalyzer().analyze(rows).to_json_dict()
        except Exception:
            return None

    def _tool_content_to_text(self, content: Any) -> str:
        """将 ToolMessage.content 规范为可 json.loads 的字符串（兼容 LangChain 文本块列表）。"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    t = block.get("text")
                    if isinstance(t, str):
                        parts.append(t)
            return "".join(parts)
        return str(content)

    def _safe_json(self, content: Any) -> dict[str, Any]:
        raw_for_fallback: Any = content
        text = self._tool_content_to_text(content).strip()
        if not text:
            if isinstance(content, list):
                return {"raw": content}
            return {"raw_text": str(content)}
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
            return {"value": data}
        except json.JSONDecodeError:
            return {"raw_text": text, "raw": raw_for_fallback}


def to_suggestions_only(full: AnalysisResponse) -> SuggestionsOnlyResponse:
    """从完整分析结果中抽出建议列表。"""
    s = full.suggestions or {}
    dialect_val = s.get("dialect")
    dialect_str = dialect_val if isinstance(dialect_val, str) else None
    raw_items = s.get("items")
    items: list[dict[str, Any]] = []
    if isinstance(raw_items, list):
        for it in raw_items:
            if isinstance(it, dict):
                items.append(it)
    return SuggestionsOnlyResponse(dialect=dialect_str, items=items)
