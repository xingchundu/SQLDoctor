"""
backend.api.routes.analysis：SQL 分析流水线 HTTP API。

职责：校验入参、装配 ToolRuntimeDeps、调用 AnalysisApplicationService。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.config import Settings, get_settings
from backend.dependencies import optional_analysis_session
from backend.services.analysis_service import (
    AnalysisApplicationService,
    AnalysisRequest,
    AnalysisResponse,
)
from backend.services.runtime_factory import ToolRuntimeFactory

router = APIRouter()


@router.post("/run", response_model=AnalysisResponse)
async def run_analysis(
    body: AnalysisRequest,
    settings: Settings = Depends(get_settings),
) -> AnalysisResponse:
    factory = ToolRuntimeFactory(settings)
    svc = AnalysisApplicationService()
    async with optional_analysis_session(settings, body.database_url) as session:
        runtime = await factory.build(session)
        return await svc.analyze(body, runtime=runtime)
