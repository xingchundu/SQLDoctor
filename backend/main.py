"""
backend.main：FastAPI 应用工厂与 ASGI 入口。

职责：注册路由、中间件、生命周期与全局异常处理。
"""

from __future__ import annotations

import backend.env_bootstrap  # noqa: F401 — 先于 Hub 相关 import 加载 .env / HF_ENDPOINT

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app_exception import AppException, ConfigurationError
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

    async def _load_kb_background() -> None:
        if not settings.kb_enabled:
            return
        try:
            from kb.bootstrap import load_or_build_retriever

            app.state.kb_retriever = await load_or_build_retriever(settings)
            logger.info("知识库已就绪，RAG 检索可用")
        except Exception as exc:
            extra = ""
            if isinstance(exc, ConfigurationError) and exc.details:
                extra = f" details={exc.details}"
            logger.warning("知识库未加载，RAG 将降级为空检索：%s%s", exc, extra)

    async with lifespan_cache(settings) as cache:
        app.state.cache = cache
        kb_task = asyncio.create_task(_load_kb_background())
        try:
            yield
        finally:
            kb_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await kb_task
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

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/docs", status_code=307)

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
