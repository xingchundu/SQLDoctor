"""
backend.services.analysis_service：触发 LangGraph 流水线并整理 HTTP 响应模型。

职责：隔离 FastAPI 与 agent.graph 的交互细节。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from pydantic import BaseModel, Field

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


class AnalysisResponse(BaseModel):
    """分析接口出参：原始消息轨迹 + 提取后的结构化片段。"""

    messages: list[dict[str, Any]] = Field(default_factory=list)
    parse: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None
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
        return AnalysisResponse(
            messages=messages_out,
            parse=parse_data,
            plan=plan_data,
            suggestions=suggestions_data,
            rewrite=rewrite_data,
        )

    def _safe_json(self, content: str | list[str | dict]) -> dict[str, Any]:
        if isinstance(content, list):
            return {"raw": content}
        try:
            data = json.loads(str(content))
            if isinstance(data, dict):
                return data
            return {"value": data}
        except json.JSONDecodeError:
            return {"raw_text": str(content)}
