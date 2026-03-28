"""
kb.models：知识条目与检索结果的 Pydantic 模型。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RetrievedChunk(BaseModel):
    """单条检索命中。"""

    content: str = Field(description="文本片段")
    source: str = Field(default="", description="来源文件名或标识")
    category: str = Field(default="", description="slow_sql | index_rule | company")
    score: float | None = Field(default=None, description="距离/相似度分数，越小通常越近")


class RagContextBundle(BaseModel):
    """打包给 LLM 的检索上下文与可序列化元数据。"""

    prompt_block: str = Field(description="拼入 Prompt 的纯文本块")
    chunks: list[RetrievedChunk] = Field(default_factory=list)
