"""
kb.retriever：FAISS 向量检索封装（异步包装）。

职责：根据 SQL + 计划摘要构造查询，返回 RagContextBundle。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_community.vectorstores import FAISS
from langchain_core.embeddings import Embeddings

from kb.models import RagContextBundle, RetrievedChunk


class KnowledgeRetriever:
    """基于 LangChain FAISS 的异步检索器。"""

    def __init__(self, vectorstore: FAISS) -> None:
        self._vs = vectorstore

    async def retrieve_for_sql(
        self,
        sql: str,
        *,
        plan_analysis: dict[str, Any] | None = None,
        k: int = 8,
        dialect: str | None = None,
    ) -> RagContextBundle:
        plan_blob = json.dumps(plan_analysis or {}, ensure_ascii=False)[:3000]
        head = (
            f"目标数据库方言: {dialect}\n\n"
            if dialect and str(dialect).strip()
            else ""
        )
        query = f"{head}SQL:\n{sql}\n\n计划/规则分析摘要:\n{plan_blob}"

        def _search() -> list[tuple[Any, float]]:
            return self._vs.similarity_search_with_score(query, k=k)

        pairs = await asyncio.to_thread(_search)
        chunks: list[RetrievedChunk] = []
        lines: list[str] = []
        for doc, score in pairs:
            meta = doc.metadata or {}
            ch = RetrievedChunk(
                content=doc.page_content,
                source=str(meta.get("source", "")),
                category=str(meta.get("category", "")),
                score=float(score),
            )
            chunks.append(ch)
            lines.append(
                f"### 片段 [{ch.category or 'doc'}] {ch.source}\n{doc.page_content.strip()}"
            )
        block = (
            "[KNOWLEDGE_BASE_RETRIEVAL]\n"
            "以下片段来自公司内部知识库（慢 SQL 案例、索引规则、经验）。\n"
            "请与 EXPLAIN 对照使用；若冲突以 EXPLAIN 与统计信息为准。\n\n"
            + "\n\n---\n\n".join(lines)
            if lines
            else "[KNOWLEDGE_BASE_RETRIEVAL]\n（本轮未命中片段）\n"
        )
        return RagContextBundle(prompt_block=block, chunks=chunks)


def load_faiss_store(index_dir: str, embeddings: Embeddings) -> FAISS:
    return FAISS.load_local(
        index_dir,
        embeddings,
        allow_dangerous_deserialization=True,
    )
