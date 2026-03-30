"""
agent.sql_agent：基于 LangChain 的 SQL 诊断 Agent（EXPLAIN → 规则分析 → LLM 建议）。

职责：串联异步 EXPLAIN、ExecutionPlanAnalyzer 与大模型；通过 Pydantic + 严格 Prompt
      约束 LLM 仅输出 { issues, suggestions, optimized_sql } JSON 语义。
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field

from analyzer.plan_analyzer import ExecutionPlanAnalyzer, PlanAnalysisReport
from app_exception import AgentError, ConfigurationError, OptimizerError, ParseError
from db.config import SqlDialect
from db.db_client import ExplainDbClient
from kb.retriever import KnowledgeRetriever
from optimizer.rewriter import SqlRewriteService


class SqlAgentLlmOutput(BaseModel):
    """与大模型约定且必须遵守的 JSON 结构（字段名固定）。"""

    model_config = ConfigDict(extra="forbid")

    issues: list[str] = Field(
        default_factory=list,
        description="结合 EXPLAIN 与规则分析得到的问题要点",
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="可执行的优化建议，须引用计划中的依据",
    )
    optimized_sql: str = Field(
        default="",
        description="改写后的 SQL；无法安全改写时填原始 SQL",
    )


class SqlAgentPipelineResult(BaseModel):
    """整条流水线对外结果。"""

    explain: dict[str, Any] = Field(default_factory=dict)
    plan_analysis: dict[str, Any] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    optimized_sql: str = ""
    rag_chunks: list[dict[str, Any]] = Field(
        default_factory=list,
        description="RAG 检索命中，含 source/category/score/content 摘要",
    )

    def to_full_json(self) -> dict[str, Any]:
        return {
            "explain": self.explain,
            "plan_analysis": self.plan_analysis,
            "issues": self.issues,
            "suggestions": self.suggestions,
            "optimized_sql": self.optimized_sql,
            "rag_chunks": self.rag_chunks,
        }


_SQL_AGENT_SYSTEM = """你是资深数据库性能顾问，根据「原始 SQL」「EXPLAIN 结构化结果」「规则分析 JSON」\
以及可选的「知识库检索片段」综合作答。

硬性输出要求（违反视为错误）：
1) 最终回答必须是 **单一 JSON 对象**，且顶层键 **只能** 为：
   "issues"（字符串数组）、"suggestions"（字符串数组）、"optimized_sql"（单个字符串）。
2) 不允许输出 Markdown、代码围栏、注释或任何 JSON 之外的文字。
3) "issues" 与 "suggestions" 中的每一条须优先与 EXPLAIN / 规则分析对齐；\
可引用知识库中的案例或规则作为补充说明，但若与 EXPLAIN 冲突必须以 EXPLAIN 为准。
4) 请显式提及访问类型(type)、索引(key)、估算行数(rows)、Extra 中的线索（如 Using filesort）等（若存在）。
5) "optimized_sql" 必须是完整可执行的 SQL，且与原始 SQL 语义等价：不得删除或省略 WHERE、JOIN、\
ON、GROUP BY、HAVING、ORDER BY、LIMIT 等子句；仅允许等价改写。若证据不足或改写风险高，请原样返回原始 SQL。
6) 不要编造真实环境中不存在的索引名；若建议新建索引，请用「建议考虑」类表述并说明列与谓词。"""


def _openai_compatible_base_url(base: str | None) -> str:
    u = (base or "http://127.0.0.1:11434").rstrip("/")
    if u.endswith("/v1"):
        return u
    return f"{u}/v1"


def build_chat_openai_from_settings() -> ChatOpenAI:
    """从 backend 配置构造 OpenAI 兼容 Chat 客户端（Ollama / vLLM 等）。"""
    from backend.config import effective_llm_model, get_settings

    settings = get_settings()
    model_name = effective_llm_model(settings)
    base = settings.llm_openai_base_url or _openai_compatible_base_url(
        settings.ollama_base_url
    )
    api_key = settings.llm_api_key or "ollama"
    return ChatOpenAI(
        model=model_name,
        base_url=base,
        api_key=api_key,
        temperature=0.1,
        timeout=120.0,
    )


def _strip_json_fence(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", t, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return t


def _parse_llm_json_to_output(content: str) -> SqlAgentLlmOutput:
    raw = _strip_json_fence(content)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AgentError(
            "大模型返回非合法 JSON",
            details={"preview": raw[:500], "reason": str(exc)},
        ) from exc
    if not isinstance(data, dict):
        raise AgentError(
            "大模型 JSON 根类型错误",
            details={"type": type(data).__name__},
        )
    try:
        return SqlAgentLlmOutput.model_validate(data)
    except Exception as exc:
        raise AgentError(
            "大模型 JSON 不符合约定 schema",
            details={"reason": str(exc), "keys": list(data.keys())},
        ) from exc


def _coerce_structured_llm_output(out: Any) -> SqlAgentLlmOutput:
    if isinstance(out, SqlAgentLlmOutput):
        return out
    if isinstance(out, dict):
        return SqlAgentLlmOutput.model_validate(out)
    raise AgentError(
        "结构化输出类型无法识别",
        details={"type": type(out).__name__},
    )


def _format_chat_history(history: list[dict[str, str]] | None) -> str:
    """将多轮对话压缩进单次 LLM HumanMessage（仅用于追问上下文）。"""
    if not history:
        return ""
    lines: list[str] = []
    for turn in history[-16:]:
        role = (turn.get("role") or "").strip()
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        label: Literal["用户", "助手"] = "用户" if role == "user" else "助手"
        lines.append(f"{label}: {content}")
    if not lines:
        return ""
    return "[PRIOR_CONVERSATION]\n" + "\n\n".join(lines) + "\n\n"


def _message_content_to_text(content: str | list[str | dict]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and "text" in block:
            parts.append(str(block["text"]))
        else:
            parts.append(str(block))
    return "".join(parts)


class SqlAgent:
    """
    SQL Agent：EXPLAIN → 计划规则分析 → LangChain 大模型生成 issues / suggestions / optimized_sql。
    """

    def __init__(
        self,
        llm: BaseChatModel,
        *,
        plan_analyzer: ExecutionPlanAnalyzer | None = None,
        explain_timeout_seconds: float = 30.0,
        retriever: KnowledgeRetriever | None = None,
        rag_top_k: int | None = None,
    ) -> None:
        self._llm = llm
        self._plan_analyzer = plan_analyzer or ExecutionPlanAnalyzer()
        self._explain_timeout_seconds = explain_timeout_seconds
        self._retriever = retriever
        self._rag_top_k = rag_top_k

    @classmethod
    def from_backend_settings(cls) -> SqlAgent:
        return cls(build_chat_openai_from_settings())

    async def run(
        self,
        sql: str,
        *,
        database_url: str | None = None,
        dialect: SqlDialect,
        analyze: bool = False,
        explain_result: dict[str, Any] | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> SqlAgentPipelineResult:
        """
        执行完整流水线。

        explain_result: 若已在外部执行 EXPLAIN，可传入 ExplainDbClient.explain() 的 dict，将跳过连库；
        此时可不传 database_url。
        """
        if not sql or not sql.strip():
            raise AgentError("SQL 为空", details={})
        if explain_result is None and not database_url:
            raise ConfigurationError(
                "需要提供 database_url 以执行 EXPLAIN，或直接传入 explain_result",
                details={},
            )

        explain = await self._resolve_explain(
            sql,
            database_url=database_url or "",
            dialect=dialect,
            analyze=analyze,
            explain_result=explain_result,
        )
        plan_report = self._run_plan_analyzer(explain)
        rag_block = ""
        rag_meta: list[dict[str, Any]] = []
        if self._retriever is not None:
            k = self._rag_top_k if self._rag_top_k is not None else 8
            bundle = await self._retriever.retrieve_for_sql(
                sql,
                plan_analysis=plan_report.to_json_dict(),
                k=k,
            )
            rag_block = bundle.prompt_block
            rag_meta = [
                {
                    "source": c.source,
                    "category": c.category,
                    "score": c.score,
                    "preview": c.content[:400] + ("…" if len(c.content) > 400 else ""),
                }
                for c in bundle.chunks
            ]
        llm_out = await self._run_llm(
            sql,
            explain,
            plan_report,
            rag_block=rag_block,
            history=history,
        )
        sqlglot_sql = ""
        try:
            rw_rep = await SqlRewriteService().build_report(sql.strip(), dialect)
            if rw_rep.candidates:
                sqlglot_sql = (rw_rep.candidates[0].sql_text or "").strip()
        except (ParseError, OptimizerError):
            pass
        llm_sql = (llm_out.optimized_sql or "").strip()
        if sqlglot_sql:
            optimized_sql = sqlglot_sql
        elif llm_sql:
            optimized_sql = llm_sql
        else:
            optimized_sql = sql.strip()
        return SqlAgentPipelineResult(
            explain=explain,
            plan_analysis=plan_report.to_json_dict(),
            issues=llm_out.issues,
            suggestions=llm_out.suggestions,
            optimized_sql=optimized_sql,
            rag_chunks=rag_meta,
        )

    async def _resolve_explain(
        self,
        sql: str,
        *,
        database_url: str,
        dialect: SqlDialect,
        analyze: bool,
        explain_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if explain_result is not None:
            return dict(explain_result)
        client = ExplainDbClient(database_url, dialect=dialect)
        try:
            return await client.explain(
                sql,
                analyze=analyze,
                timeout_seconds=self._explain_timeout_seconds,
            )
        finally:
            await client.close()

    def _run_plan_analyzer(self, explain: dict[str, Any]) -> PlanAnalysisReport:
        try:
            return self._plan_analyzer.analyze(explain)
        except Exception as exc:
            raise AgentError(
                "执行计划规则分析失败",
                details={"reason": str(exc)},
            ) from exc

    def _build_messages(
        self,
        sql: str,
        explain: dict[str, Any],
        plan_report: PlanAnalysisReport,
        *,
        rag_block: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> list[BaseMessage]:
        explain_json = json.dumps(explain, ensure_ascii=False, indent=2)
        plan_json = json.dumps(plan_report.to_json_dict(), ensure_ascii=False, indent=2)
        rag_section = f"{rag_block}\n\n" if rag_block else ""
        hist_section = _format_chat_history(history)
        human = (
            "以下是待诊断内容，请只输出满足系统提示约束的 JSON 对象。\n\n"
            f"{hist_section}"
            f"{rag_section}"
            f"[ORIGINAL_SQL]\n{sql}\n\n"
            f"[EXPLAIN_JSON]\n{explain_json}\n\n"
            f"[RULE_BASED_PLAN_ANALYSIS]\n{plan_json}\n"
        )
        return [SystemMessage(content=_SQL_AGENT_SYSTEM), HumanMessage(content=human)]

    async def _run_llm(
        self,
        sql: str,
        explain: dict[str, Any],
        plan_report: PlanAnalysisReport,
        *,
        rag_block: str = "",
        history: list[dict[str, str]] | None = None,
    ) -> SqlAgentLlmOutput:
        messages = self._build_messages(
            sql,
            explain,
            plan_report,
            rag_block=rag_block,
            history=history,
        )
        try:
            structured = self._llm.with_structured_output(SqlAgentLlmOutput)
            out = await structured.ainvoke(messages)
            return _coerce_structured_llm_output(out)
        except AgentError:
            raise
        except Exception:
            return await self._run_llm_raw_json(messages)

    async def _run_llm_raw_json(
        self,
        messages: list[BaseMessage],
    ) -> SqlAgentLlmOutput:
        try:
            resp = await self._llm.ainvoke(messages)
            text = _message_content_to_text(resp.content)
            return _parse_llm_json_to_output(text)
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError(
                "大模型调用失败",
                details={"reason": str(exc)},
            ) from exc
