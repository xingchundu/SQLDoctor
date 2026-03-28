"""
kb.ingest：从种子 Markdown 切块并构建 FAISS 索引。

职责：离线/启动时建库；所有重计算在同步函数中由 asyncio.to_thread 调用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app_exception import ConfigurationError

_CATEGORY_BY_STEM = {
    "slow_sql_cases": "slow_sql",
    "index_rules": "index_rule",
    "company_experience": "company",
}


def _load_markdown_docs(seed_dir: Path) -> list[Document]:
    if not seed_dir.is_dir():
        raise ConfigurationError(
            "知识库种子目录不存在",
            details={"path": str(seed_dir)},
        )
    docs: list[Document] = []
    for path in sorted(seed_dir.glob("*.md")):
        stem = path.stem
        category = _CATEGORY_BY_STEM.get(stem, "general")
        text = path.read_text(encoding="utf-8")
        docs.append(
            Document(
                page_content=text,
                metadata={
                    "source": path.name,
                    "category": category,
                    "path": str(path),
                },
            )
        )
    if not docs:
        raise ConfigurationError(
            "种子目录下没有 .md 文档",
            details={"path": str(seed_dir)},
        )
    return docs


def _split_documents(docs: list[Document]) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=600,
        chunk_overlap=100,
        separators=["\n## ", "\n### ", "\n\n", "\n", " "],
    )
    return splitter.split_documents(docs)


def build_faiss_index(
    seed_dir: str | Path,
    out_dir: str | Path,
    embeddings: Embeddings,
) -> Path:
    """
    从 seed_dir 读取全部 .md，切块后写入 FAISS 到 out_dir。
    """
    seed_path = Path(seed_dir)
    out_path = Path(out_dir)
    raw_docs = _load_markdown_docs(seed_path)
    chunks = _split_documents(raw_docs)
    out_path.mkdir(parents=True, exist_ok=True)
    vs = FAISS.from_documents(chunks, embeddings)
    vs.save_local(str(out_path))
    return out_path


def faiss_index_exists(index_dir: str | Path) -> bool:
    p = Path(index_dir)
    return p.is_dir() and any(p.glob("*.faiss"))
