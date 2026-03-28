"""
backend.main：FastAPI 应用工厂与 ASGI 入口。

职责：注册路由、中间件、生命周期与全局异常处理。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app_exception import AppException
from backend.api.router import api_router
from backend.config import get_settings
from backend.dependencies import lifespan_cache
from backend.error_handlers import app_exception_handler, unhandled_exception_handler
from db.engine import engine_factory

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.kb_retriever = None
    if settings.kb_enabled:
        try:
            from kb.bootstrap import load_or_build_retriever

            app.state.kb_retriever = await load_or_build_retriever(settings)
        except Exception as exc:
            logger.warning("知识库未加载，RAG 将降级为空检索：%s", exc)
    async with lifespan_cache(settings) as cache:
        app.state.cache = cache
        yield
    await engine_factory.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    origins = [o.strip() for o in settings.api_cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(AppException, app_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
    app.include_router(api_router, prefix="/api")
    return app


app = create_app()


def main() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=s.api_port,
        reload=bool(s.debug),
    )


if __name__ == "__main__":
    main()
