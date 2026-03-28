"""
kb.bootstrap：在应用启动时加载或构建 FAISS 索引。

职责：封装路径解析与 build/load 决策，供 FastAPI lifespan 调用。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from langchain_core.embeddings import Embeddings

from app_exception import ConfigurationError
from kb.ingest import build_faiss_index, faiss_index_exists
from kb.retriever import KnowledgeRetriever, load_faiss_store


def _default_seed_dir() -> Path:
    return Path(__file__).resolve().parent / "seed"


def _resolve_index_dir(settings: Any) -> Path:
    return Path(settings.kb_faiss_path).resolve()


def _resolve_seed_dir(settings: Any) -> Path:
    p = Path(settings.kb_seed_path)
    if p.is_absolute():
        return p
    return (Path.cwd() / p).resolve()


def build_embeddings_from_settings(settings: Any) -> Embeddings:
    from kb.embeddings import build_kb_embeddings

    return build_kb_embeddings(
        settings.kb_embedding_model,
        use_openai_compatible=settings.kb_use_openai_embeddings,
        openai_base_url=settings.kb_openai_embedding_base_url,
        openai_api_key=settings.kb_openai_embedding_api_key or settings.llm_api_key,
    )


async def load_or_build_retriever(settings: Any) -> KnowledgeRetriever | None:
    """
    若 kb_enabled=False 返回 None；否则加载已有索引，不存在则从种子构建。
    """
    if not getattr(settings, "kb_enabled", True):
        return None

    index_dir = _resolve_index_dir(settings)
    seed_dir = _resolve_seed_dir(settings)
    embeddings = build_embeddings_from_settings(settings)

    if not faiss_index_exists(index_dir):
        if not seed_dir.is_dir():
            raise ConfigurationError(
                "知识库索引不存在且种子目录无效",
                details={"index_dir": str(index_dir), "seed_dir": str(seed_dir)},
            )

        def _build() -> None:
            build_faiss_index(seed_dir, index_dir, embeddings)

        await asyncio.to_thread(_build)

    def _load() -> KnowledgeRetriever:
        vs = load_faiss_store(str(index_dir), embeddings)
        return KnowledgeRetriever(vs)

    return await asyncio.to_thread(_load)
