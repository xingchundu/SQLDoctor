"""
agent.runtime：工具运行时依赖与请求级流水线缓冲区（ContextVar）。

职责：在 @tool 与 FastAPI 请求之间传递只读服务实例与中间结果，避免全局可变单例。

使用 dataclass 而非 Pydantic BaseModel，避免仅放在 TYPE_CHECKING 下的类型在运行期未解析导致
「class not fully defined」错误（Pydantic v2 + Python 3.14 等环境）。
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from analyzer.models import ParseResult, UnifiedPlan
from analyzer.parser import SqlParseAnalyzer
from analyzer.plan_fetcher import ExecutionPlanAnalyzer
from optimizer.rewriter import SqlRewriteService
from optimizer.suggestions import OptimizationSuggestionService


@dataclass
class PipelineBuffer:
    """单次流水线在工具之间的内存结果聚合（非线程全局）。"""

    parse: ParseResult | None = None
    plan: UnifiedPlan | None = None


@dataclass
class ToolRuntimeDeps:
    """注入到工具闭包外的运行时依赖容器。"""

    parse_analyzer: SqlParseAnalyzer
    suggestion_service: OptimizationSuggestionService
    rewriter: SqlRewriteService
    plan_analyzer: ExecutionPlanAnalyzer | None = None
    explain_timeout_seconds: float = 30.0


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
