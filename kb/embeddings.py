"""
kb.embeddings：构建 LangChain Embeddings（默认 HuggingFace 本地向量模型）。

职责：与 FAISS 存盘/加载使用同一套向量函数，避免检索漂移。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app_exception import ConfigurationError

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings


def build_kb_embeddings(
    model_name: str,
    *,
    use_openai_compatible: bool = False,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
) -> "Embeddings":
    """
    构建用于 FAISS 的 Embeddings。

    use_openai_compatible=True 时使用 OpenAI 兼容 /v1/embeddings（如部分 vLLM / 云服务）。
    """
    if use_openai_compatible:
        from langchain_openai import OpenAIEmbeddings

        if not openai_base_url:
            raise ConfigurationError(
                "OpenAI 兼容向量需要配置 kb_openai_embedding_base_url",
                details={},
            )
        return OpenAIEmbeddings(
            model=model_name,
            base_url=openai_base_url.rstrip("/"),
            api_key=openai_api_key or "empty",
        )

    try:
        from langchain_huggingface import HuggingFaceEmbeddings
    except ImportError as exc:
        raise ConfigurationError(
            "缺少 langchain-huggingface / sentence-transformers，无法构建本地向量模型",
            details={"hint": "pip install langchain-huggingface sentence-transformers"},
        ) from exc

    return HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
