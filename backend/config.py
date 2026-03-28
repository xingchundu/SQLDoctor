"""
backend.config：应用级配置（环境变量 + Pydantic Settings）。

职责：集中管理 API、数据库、Redis、LLM 等运行参数，供依赖注入与各层读取。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="SQLDoctor API")
    debug: bool = Field(default=False)

    database_url: str | None = Field(
        default=None,
        description="SQLAlchemy async URL，例如 mysql+aiomysql://...",
    )
    redis_url: str | None = Field(
        default=None,
        description="redis://host:6379/0，为空则缓存降级为内存占位",
    )

    ollama_base_url: str | None = Field(default="http://127.0.0.1:11434")
    llm_model: str | None = Field(default=None, description="本地模型名，未设置则走无 LLM 规则编排")
    llm_openai_base_url: str | None = Field(
        default=None,
        description="OpenAI 兼容 Chat API 根 URL（含 /v1）；为空则由 ollama_base_url 推导",
    )
    llm_api_key: str | None = Field(
        default=None,
        description="OpenAI 兼容 API Key；为空时对 Ollama 使用占位值",
    )

    api_cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        description="逗号分隔的前端 Origin",
    )

    sql_max_length: int = Field(default=256_000)
    explain_timeout_seconds: float = Field(default=30.0)

    kb_enabled: bool = Field(default=True, description="是否启用 FAISS 知识库与 RAG 检索")
    kb_faiss_path: str = Field(default="data/kb_faiss", description="FAISS 索引目录")
    kb_seed_path: str = Field(default="kb/seed", description="种子 Markdown 相对项目根或绝对路径")
    kb_top_k: int = Field(default=8, ge=1, le=64, description="检索返回条数")
    kb_embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description="HuggingFace 模型 id 或 OpenAI 兼容 embedding 模型名",
    )
    kb_use_openai_embeddings: bool = Field(
        default=False,
        description="为 True 时使用 OpenAI 兼容 /v1/embeddings",
    )
    kb_openai_embedding_base_url: str | None = Field(
        default=None,
        description="如 https://api.openai.com/v1 或自建网关；需含 /v1",
    )
    kb_openai_embedding_api_key: str | None = Field(
        default=None,
        description="向量 API Key，默认可复用 llm_api_key",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
