"""
kb.rebuild：命令行重建 FAISS 索引（需在项目根目录、已安装依赖）。

用法：python -m kb.rebuild
"""

from __future__ import annotations

import backend.env_bootstrap  # noqa: F401 — 重建索引前应用 HF_ENDPOINT

from typing import Any

from kb.bootstrap import build_embeddings_from_settings
from kb.ingest import build_faiss_index


def _resolve_index_dir(settings: Any):
    from pathlib import Path

    return Path(settings.kb_faiss_path).resolve()


def _resolve_seed_dir(settings: Any):
    from pathlib import Path

    p = Path(settings.kb_seed_path)
    if p.is_absolute():
        return p
    return (Path.cwd() / p).resolve()


def main() -> None:
    from backend.config import get_settings

    settings = get_settings()
    emb = build_embeddings_from_settings(settings)
    idx = _resolve_index_dir(settings)
    seed = _resolve_seed_dir(settings)
    build_faiss_index(seed, idx, emb)
    print(f"FAISS 索引已写入: {idx}")


if __name__ == "__main__":
    main()
