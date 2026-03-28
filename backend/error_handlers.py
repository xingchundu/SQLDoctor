"""
backend.error_handlers：将 AppException 族映射为 JSON 响应。

职责：保持 HTTP 层与领域异常解耦，统一日志与状态码策略。
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app_exception import (
    AgentError,
    AppException,
    CacheError,
    ConfigurationError,
    DatabaseError,
    OptimizerError,
    ParseError,
    PlanAnalysisError,
)


def _status_for(exc: AppException) -> int:
    mapping: dict[type[AppException], int] = {
        ConfigurationError: status.HTTP_500_INTERNAL_SERVER_ERROR,
        DatabaseError: status.HTTP_502_BAD_GATEWAY,
        ParseError: status.HTTP_400_BAD_REQUEST,
        PlanAnalysisError: status.HTTP_422_UNPROCESSABLE_ENTITY,
        OptimizerError: status.HTTP_422_UNPROCESSABLE_ENTITY,
        AgentError: status.HTTP_500_INTERNAL_SERVER_ERROR,
        CacheError: status.HTTP_503_SERVICE_UNAVAILABLE,
    }
    return mapping.get(type(exc), status.HTTP_500_INTERNAL_SERVER_ERROR)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    _ = request
    body: dict[str, Any] = {"error": exc.to_payload()}
    return JSONResponse(status_code=_status_for(exc), content=body)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    _ = request
    if isinstance(exc, StarletteHTTPException):
        raise exc
    wrapped = AppException(
        "INTERNAL_ERROR",
        str(exc),
        details={"type": type(exc).__name__},
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": wrapped.to_payload()},
    )
