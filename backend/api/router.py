"""
backend.api.router：挂载子路由。

职责：为 main 提供单一 include_router 入口。
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.api.routes import analysis as analysis_routes
from backend.api.routes import db_test as db_test_routes
from backend.api.routes import health as health_routes
from backend.api.routes import rag_diagnose as rag_routes

api_router = APIRouter()
api_router.include_router(health_routes.router, tags=["health"])
api_router.include_router(analysis_routes.router, prefix="/analysis", tags=["analysis"])
api_router.include_router(db_test_routes.router, prefix="/analysis", tags=["analysis"])
api_router.include_router(db_test_routes.router, prefix="/db", tags=["database"])
api_router.include_router(rag_routes.router, prefix="/rag", tags=["rag"])
