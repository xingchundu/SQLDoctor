"""
backend.api.routes.rag_diagnose：RAG 增强的 SQL 诊断 API。

职责：在生成 LLM 建议前已检索 FAISS 知识库；无库连接时 EXPLAIN 可跳过（explain_result 由 agent 内部处理）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from agent.sql_agent import SqlAgentPipelineResult
from backend.config import Settings, get_settings
from backend.services.rag_diagnose_service import (
    DiagnoseRagRequest,
    RagDiagnoseApplicationService,
)

router = APIRouter()


def _get_kb_retriever(request: Request):
    return getattr(request.app.state, "kb_retriever", None)


@router.post("/diagnose", response_model=SqlAgentPipelineResult)
async def diagnose_with_rag(
    body: DiagnoseRagRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> SqlAgentPipelineResult:
    svc = RagDiagnoseApplicationService()
    return await svc.diagnose(
        body,
        retriever=_get_kb_retriever(request),
        kb_top_k=settings.kb_top_k,
        explain_timeout_seconds=settings.explain_timeout_seconds,
    )
