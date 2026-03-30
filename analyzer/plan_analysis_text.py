"""
将 plan_analysis（summary / details / problems）格式化为与前端一致的 Markdown 段落，
供 Streamlit、CLI 或日志输出复用。
"""

from __future__ import annotations

import json
import sys
from typing import Any


def format_plan_analysis_sections(plan_analysis: dict[str, Any] | None) -> str:
    """
    返回 Markdown：③ 风险摘要、③‑附 简要说明、③‑详情（按规则合并）。
    无有效内容时返回空字符串。
    """
    if not plan_analysis or not isinstance(plan_analysis, dict):
        return ""

    problems = plan_analysis.get("problems") or []
    summary = plan_analysis.get("summary") or {}
    details = plan_analysis.get("details") or []

    has_problems = isinstance(problems, list) and len(problems) > 0
    has_summary = isinstance(summary, dict) and (
        summary.get("total_steps") is not None or summary.get("unique_rules") is not None
    )
    has_details = isinstance(details, list) and len(details) > 0

    if not has_problems and not has_summary and not has_details:
        return ""

    blocks: list[str] = []

    if has_summary:
        sum_lines = [
            "### ③ 执行计划风险摘要（MySQL / PostgreSQL / Oracle 统一规则）",
            "",
            f"- **综合风险**：{summary.get('risk_level') or plan_analysis.get('risk_level') or '—'}",
            f"- **计划步骤数**：{summary.get('total_steps', '—')}",
            (
                f"- **规则种类**：{summary.get('unique_rules', '—')}（累计触发 "
                f"{summary.get('total_rule_hits', '—')} 次）"
            ),
        ]
        rc = summary.get("rule_step_counts")
        if isinstance(rc, dict) and rc:
            dist = "、".join(f"{k}×{v}" for k, v in rc.items())
            sum_lines.append(f"- **类型分布**：{dist}")
        blocks.append("\n".join(sum_lines))

    if has_details:
        detail_lines = ["### ③‑附 简要说明", ""]
        for d in details:
            if isinstance(d, str) and d.strip():
                detail_lines.append(f"- {d}")
        if len(detail_lines) > 2:
            blocks.append("\n".join(detail_lines))

    if has_problems:
        prob_lines = ["### ③‑详情 执行计划问题（按规则合并，不重复）"]
        for i, p in enumerate(problems, 1):
            if not isinstance(p, dict):
                continue
            code = p.get("code")
            code_part = f" `{code}`" if code else ""
            title = p.get("title") or "项"
            steps = p.get("affected_steps") or []
            step_txt = ""
            if isinstance(steps, list) and steps:
                step_txt = "影响步骤：" + "、".join(f"step{s}" for s in steps)
            reason = (p.get("reason") or "").strip()
            prob_lines.extend(["", f"{i}. **{title}**{code_part}", step_txt, "", reason])
        blocks.append("\n".join(prob_lines))

    return "\n\n".join(blocks)


def main() -> None:
    """从 stdin 读 JSON：整份分析响应或仅 plan_analysis 对象，向 stdout 打印三段式 Markdown。"""
    raw = sys.stdin.read()
    if not raw.strip():
        sys.stderr.write("stdin 为空；请传入 JSON（可含 plan_analysis 字段）。\n")
        sys.exit(1)
    data = json.loads(raw)
    if not isinstance(data, dict):
        sys.stderr.write("根节点须为 JSON 对象。\n")
        sys.exit(1)
    pa = data.get("plan_analysis")
    if pa is None and "problems" in data:
        pa = data
    text = format_plan_analysis_sections(pa if isinstance(pa, dict) else None)
    sys.stdout.write(text)
    if text and not text.endswith("\n"):
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
