"""
在导入 huggingface_hub / sentence_transformers 之前加载 .env 并设置 HF 端点。

huggingface_hub 只读 os.environ；仅靠 Pydantic 读 .env 不会写入环境变量，导致仍请求 huggingface.co。
"""

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


def apply() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_FILE, override=False)
    except ImportError:
        pass

    official = os.environ.get("SQLDOCTOR_HF_OFFICIAL", "").lower() in ("1", "true", "yes")
    if official:
        return
    if os.environ.get("HF_ENDPOINT", "").strip():
        return
    # 与 run.bat 默认一致：未配置时走镜像，避免 WinError 10060
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"


apply()
