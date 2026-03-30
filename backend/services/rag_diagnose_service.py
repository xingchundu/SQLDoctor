"""
backend.services.rag_diagnose_service：RAG + SqlAgent 一体化诊断。

职责：装配 KnowledgeRetriever（若已加载）、调用 SqlAgent.run，返回带 rag_chunks 的结果。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent.sql_agent import SqlAgent, SqlAgentPipelineResult, build_chat_openai_from_settings
from app_exception import ConfigurationError
from db.config import SqlDialect
from kb.retriever import KnowledgeRetriever


class ChatTurn(BaseModel):
    """多轮对话中的一条（供本地模型理解追问上下文）。"""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class DiagnoseRagRequest(BaseModel):
    """RAG 诊断请求。"""

    sql: str = Field(min_length=1)
    dialect: str = Field(default="mysql")
    database_url: str | None = Field(default=None)
    analyze: bool = Field(default=False)
    history: list[ChatTurn] = Field(
        default_factory=list,
        description="当前轮之前的对话，不含本条 sql",
    )


class RagDiagnoseApplicationService:
    """应用服务：带知识库检索的 LLM 诊断。"""

    async def diagnose(
        self,
        body: DiagnoseRagRequest,
        *,
        retriever: KnowledgeRetriever | None,
        kb_top_k: int,
        explain_timeout_seconds: float,
    ) -> SqlAgentPipelineResult:
        llm = build_chat_openai_from_settings()
        try:
            dialect = SqlDialect(body.dialect)
        except ValueError as exc:
            raise ConfigurationError(
                "不支持的 dialect",
                details={"dialect": body.dialect},
            ) from exc
        agent = SqlAgent(
            llm,
            retriever=retriever,
            rag_top_k=kb_top_k,
            explain_timeout_seconds=explain_timeout_seconds,
        )
        hist = [{"role": t.role, "content": t.content} for t in body.history[-24:]]
        return await agent.run(
            body.sql,
            database_url=body.database_url,
            dialect=dialect,
            analyze=body.analyze,
            history=hist,
        )
