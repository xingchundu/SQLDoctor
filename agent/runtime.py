"""
agent.runtime：工具运行时依赖与请求级流水线缓冲区（ContextVar）。

职责：在 @tool 与 FastAPI 请求之间传递只读服务实例与中间结果，避免全局可变单例。
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from analyzer.models import ParseResult, UnifiedPlan

if TYPE_CHECKING:
    from analyzer.parser import SqlParseAnalyzer
    from analyzer.plan_fetcher import ExecutionPlanAnalyzer
    from optimizer.rewriter import SqlRewriteService
    from optimizer.suggestions import OptimizationSuggestionService


class PipelineBuffer(BaseModel):
    """单次流水线在工具之间的内存结果聚合（非线程全局）。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    parse: ParseResult | None = None
    plan: UnifiedPlan | None = None


class ToolRuntimeDeps(BaseModel):
    """注入到工具闭包外的运行时依赖容器。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    parse_analyzer: SqlParseAnalyzer
    plan_analyzer: ExecutionPlanAnalyzer | None = Field(default=None)
    suggestion_service: OptimizationSuggestionService
    rewriter: SqlRewriteService
    explain_timeout_seconds: float = Field(default=30.0)


RUNTIME_CTX: ContextVar[ToolRuntimeDeps | None] = ContextVar(
    "RUNTIME_CTX",
    default=None,
)
BUFFER_CTX: ContextVar[PipelineBuffer | None] = ContextVar(
    "BUFFER_CTX",
    default=None,
)


@dataclass(frozen=True)
class AgentContextBinding:
    """ContextVar.set 的 token 与缓冲区句柄。"""

    runtime_token: object
    buffer_token: object
    buffer: PipelineBuffer


def bind_agent_context(deps: ToolRuntimeDeps) -> AgentContextBinding:
    """绑定单次请求的运行时与缓冲区；必须在 finally 中调用 unbind_agent_context。"""
    buffer = PipelineBuffer()
    rt_tok = RUNTIME_CTX.set(deps)
    buf_tok = BUFFER_CTX.set(buffer)
    return AgentContextBinding(runtime_token=rt_tok, buffer_token=buf_tok, buffer=buffer)


def unbind_agent_context(binding: AgentContextBinding) -> None:
    RUNTIME_CTX.reset(binding.runtime_token)  # type: ignore[arg-type]
    BUFFER_CTX.reset(binding.buffer_token)  # type: ignore[arg-type]
