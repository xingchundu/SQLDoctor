"""
analyzer.plan_analyzer：执行计划（EXPLAIN）结构化分析。

职责：从 EXPLAIN 结果（统一 steps 或原始行）识别全表扫描、未走索引、估算行数过大、
      filesort、临时表等模式；输出 problems、risk_level、details；每条问题附带原因说明。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app_exception import PlanAnalysisError

RiskLevel = Literal["low", "medium", "high"]


class PlanProblemItem(BaseModel):
    """按规则合并后的一条问题（同一 code 多步只出现一次）。"""

    code: str = Field(description="稳定机器可读码")
    title: str = Field(description="短标题")
    reason: str = Field(description="为何构成问题及可能影响")
    affected_steps: list[int] = Field(
        default_factory=list,
        description="触发的计划步骤编号（从 1 起，与 EXPLAIN 行顺序一致）",
    )
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="代表性字段快照，如 type/key/rows/extra",
    )


class PlanAnalysisReport(BaseModel):
    """计划分析：problems 已去重；summary 与详情分离。"""

    problems: list[PlanProblemItem] = Field(default_factory=list)
    risk_level: RiskLevel = "low"
    summary: dict[str, Any] = Field(
        default_factory=dict,
        description="统计摘要（步数、触发次数、规则分布、综合风险），不混入逐条建议正文",
    )
    details: list[str] = Field(
        default_factory=list,
        description="简短人读摘要（少量行），与 problems 中的 reason 不重复堆砌",
    )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "problems": [p.model_dump() for p in self.problems],
            "risk_level": self.risk_level,
            "summary": dict(self.summary),
            "details": list(self.details),
        }


class PlanAnalyzerConfig(BaseModel):
    """可调阈值。"""

    rows_warn: int = Field(
        default=10_000,
        ge=1,
        description="估算行数超过该值视为 rows 过大（警告级）",
    )
    rows_severe: int = Field(
        default=500_000,
        ge=1,
        description="估算行数超过该值在合并全表扫描等场景下推高整体风险",
    )


def _norm_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _coerce_rows(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def _extract_steps(explain_result: Any) -> list[dict[str, Any]]:
    """接受 ExplainDbClient 返回值、含 steps 的 dict、或原始 EXPLAIN 行列表。"""
    if explain_result is None:
        return []
    if isinstance(explain_result, list):
        return [_flatten_step(r) for r in explain_result if isinstance(r, dict)]
    if isinstance(explain_result, dict):
        steps = explain_result.get("steps")
        if isinstance(steps, list):
            return [_flatten_step(s) for s in steps if isinstance(s, dict)]
        if any(
            k in explain_result
            for k in ("type", "key", "rows", "extra", "Type", "Key", "Rows", "Extra")
        ):
            return [_flatten_step(explain_result)]
        return []
    raise PlanAnalysisError(
        "EXPLAIN 结果类型不支持",
        details={"type": type(explain_result).__name__},
    )


def _flatten_step(row: dict[str, Any]) -> dict[str, Any]:
    """统一为小写键，便于规则匹配。"""
    out: dict[str, Any] = {}
    for k, v in row.items():
        out[str(k).lower()] = v
    if "type" not in out and "select_type" in out:
        out["type"] = out.get("select_type")
    return out


def _mysql_access_type(step: dict[str, Any]) -> str | None:
    """从合并字段（如 SIMPLE | ALL）或原始 type 列取出访问类型。"""
    t = _norm_str(step.get("type"))
    if not t:
        return None
    if "|" in t:
        return t.split("|")[-1].strip().upper() or None
    return t.upper()


def _is_full_table_scan(step: dict[str, Any]) -> bool:
    acc = _mysql_access_type(step)
    if acc == "ALL":
        return True
    typ = _norm_str(step.get("type")).lower()
    if re.search(r"\bseq\s+scan\b", typ):
        return True
    return False


def _key_is_null(step: dict[str, Any]) -> bool:
    k = step.get("key")
    if k is None:
        return True
    s = _norm_str(k).lower()
    return s in ("", "null", "none")


def _extra_text(step: dict[str, Any]) -> str:
    return _norm_str(step.get("extra"))


def _plan_text_blob(step: dict[str, Any]) -> str:
    """合并 MySQL/PG/Oracle 常见字段，便于跨方言匹配 filesort/temporary 等。"""
    parts = [
        _norm_str(step.get("type")),
        _norm_str(step.get("extra")),
        _norm_str(step.get("filter")),
        _norm_str(step.get("operation")),
        _norm_str(step.get("options")),
        _norm_str(step.get("remarks")),
        _norm_str(step.get("object_type")),
    ]
    return " ".join(p for p in parts if p).lower()


def _has_filesort(step: dict[str, Any]) -> bool:
    blob = _plan_text_blob(step)
    if "using filesort" in blob or "external merge" in blob:
        return True
    typ = _norm_str(step.get("type")).lower()
    return "sort" in typ and "index" not in typ


def _has_temporary(step: dict[str, Any]) -> bool:
    blob = _plan_text_blob(step)
    return "using temporary" in blob or "temporary" in blob


class ExecutionPlanAnalyzer:
    """
    执行计划分析器（核心）：输入 EXPLAIN 结果，输出 problems / risk_level / details。
    """

    def __init__(self, config: PlanAnalyzerConfig | None = None) -> None:
        self._cfg = config or PlanAnalyzerConfig()

    def analyze(self, explain_result: Any) -> PlanAnalysisReport:
        """
        分析 EXPLAIN 输出。

        explain_result:
            - db.db_client.ExplainDbClient.explain() 返回的 dict（含 steps）
            - 裸 list[dict]（MySQL 传统 EXPLAIN 多行）
            - 单行 dict
        """
        try:
            steps = _extract_steps(explain_result)
        except PlanAnalysisError:
            raise
        except Exception as exc:
            raise PlanAnalysisError(
                "解析 EXPLAIN 结构失败",
                details={"reason": str(exc)},
            ) from exc

        if not steps:
            return PlanAnalysisReport(
                problems=[],
                risk_level="low",
                summary={
                    "total_steps": 0,
                    "total_rule_hits": 0,
                    "unique_rules": 0,
                    "risk_level": "low",
                    "rule_step_counts": {},
                },
                details=["未解析到任何计划步骤（steps 为空或格式不匹配），无法做规则检测。"],
            )

        raw: list[PlanProblemItem] = []
        for idx, step in enumerate(steps):
            raw.extend(self._scan_step(idx + 1, step))

        merged = self._merge_problems_by_code(raw)
        risk = self._compute_risk_level(merged)
        summary, details = self._build_summary_and_details(steps, merged, risk)
        return PlanAnalysisReport(
            problems=merged,
            risk_level=risk,
            summary=summary,
            details=details,
        )

    def _merge_problems_by_code(self, items: list[PlanProblemItem]) -> list[PlanProblemItem]:
        by: dict[str, PlanProblemItem] = {}
        for p in items:
            if p.code not in by:
                by[p.code] = p.model_copy(deep=True)
            else:
                cur = by[p.code]
                merged_steps = sorted(set(cur.affected_steps + p.affected_steps))
                by[p.code] = cur.model_copy(update={"affected_steps": merged_steps})
        return sorted(by.values(), key=lambda x: x.code)

    def _build_summary_and_details(
        self,
        steps: list[dict[str, Any]],
        merged: list[PlanProblemItem],
        risk: RiskLevel,
    ) -> tuple[dict[str, Any], list[str]]:
        rule_counts = {p.code: len(p.affected_steps) for p in merged}
        total_hits = sum(rule_counts.values())
        summary: dict[str, Any] = {
            "total_steps": len(steps),
            "total_rule_hits": total_hits,
            "unique_rules": len(merged),
            "risk_level": risk,
            "rule_step_counts": rule_counts,
        }
        details = [
            f"共 {len(steps)} 个计划步骤；识别 {len(merged)} 类规则，累计触发 {total_hits} 次。",
            f"综合风险：{risk}。",
        ]
        if rule_counts:
            dist = "、".join(f"{k}×{v}" for k, v in sorted(rule_counts.items()))
            details.append(f"规则分布：{dist}。")
        return summary, details

    def _scan_step(self, step_num: int, step: dict[str, Any]) -> list[PlanProblemItem]:
        found: list[PlanProblemItem] = []
        evidence_base = {
            "type": step.get("type"),
            "key": step.get("key"),
            "rows": step.get("rows"),
            "extra": step.get("extra"),
        }

        if _is_full_table_scan(step):
            found.append(
                PlanProblemItem(
                    code="FULL_TABLE_SCAN",
                    title="全表扫描（type≈ALL / Seq Scan）",
                    reason=(
                        "访问类型为 ALL（或 PostgreSQL 的 Seq Scan）时，优化器通常需读取表中大量或全部数据页，"
                        "I/O 与 CPU 开销随表规模线性增长；在高并发下易成为瓶颈。"
                        "若谓词选择性高，应考虑是否能通过合适索引、分区或改写 SQL 减少扫描范围。"
                    ),
                    affected_steps=[step_num],
                    evidence=evidence_base,
                )
            )

        if _key_is_null(step):
            found.append(
                PlanProblemItem(
                    code="INDEX_NOT_USED",
                    title="未使用索引（key 为空）",
                    reason=(
                        "key 列为空表示该步骤未选用二级索引定位数据，往往依赖主键/全表或临时结构访问。"
                        "若 WHERE/JOIN/ORDER BY 与现有索引不匹配，或统计信息导致优化器放弃索引，会出现此情况。"
                        "可检查是否有覆盖谓词的复合索引，并确认 ANALYZE/统计信息及时更新。"
                    ),
                    affected_steps=[step_num],
                    evidence=evidence_base,
                )
            )

        rows = _coerce_rows(step.get("rows"))
        if rows is not None and rows >= self._cfg.rows_warn:
            severe = rows >= self._cfg.rows_severe
            found.append(
                PlanProblemItem(
                    code="ROWS_TOO_LARGE",
                    title="估算行数偏大",
                    reason=(
                        f"优化器估算本步骤需处理约 {rows:g} 行，已超过阈值 {self._cfg.rows_warn}。"
                        + (
                            "行数估计极大时，连接顺序与内存占用风险显著上升，易出现磁盘排序或哈希溢出。"
                            if severe
                            else "若实际选择性更好，可能是统计信息过期；若估计准确，应考虑限制结果集、索引或分区。"
                        )
                    ),
                    affected_steps=[step_num],
                    evidence={**evidence_base, "rows_numeric": rows},
                )
            )

        if _has_filesort(step):
            found.append(
                PlanProblemItem(
                    code="USING_FILESORT",
                    title="需要额外排序（Using filesort / Sort）",
                    reason=(
                        "extra 中出现 Using filesort（或计划节点为 Sort）表示无法在索引顺序下满足 ORDER BY / GROUP BY，"
                        "需要在内存或磁盘上排序；大数据量时延迟与临时空间占用明显增加。"
                        "可考虑与排序键一致的索引、减少 SELECT 列、或调整 SQL 避免无谓排序。"
                    ),
                    affected_steps=[step_num],
                    evidence=evidence_base,
                )
            )

        if _has_temporary(step):
            found.append(
                PlanProblemItem(
                    code="USING_TEMPORARY",
                    title="使用临时表（Using temporary）",
                    reason=(
                        "extra 中含 Using temporary（或类似聚合/去重节点）表示中间结果需暂存；"
                        "连接大表、DISTINCT、GROUP BY、UNION 等容易引发磁盘临时表。"
                        "可评估是否能改写为更简单的子查询、添加合适索引以减少物化，或限制中间结果规模。"
                    ),
                    affected_steps=[step_num],
                    evidence=evidence_base,
                )
            )

        return found

    def _compute_risk_level(self, merged: list[PlanProblemItem]) -> RiskLevel:
        if not merged:
            return "low"

        codes = {p.code for p in merged}
        total_hits = sum(len(p.affected_steps) for p in merged)
        mediumish = {"INDEX_NOT_USED", "USING_FILESORT", "USING_TEMPORARY"}
        medium_rule_count = sum(1 for p in merged if p.code in mediumish)

        if "USING_FILESORT" in codes and "USING_TEMPORARY" in codes:
            return "high"
        if "FULL_TABLE_SCAN" in codes and "ROWS_TOO_LARGE" in codes:
            return "high"
        if len(merged) >= 4 or total_hits >= 8:
            return "high"
        if medium_rule_count >= 3:
            return "high"
        if len(merged) >= 2 or total_hits >= 4:
            return "medium"
        if merged[0].code == "ROWS_TOO_LARGE":
            rows = merged[0].evidence.get("rows_numeric")
            if isinstance(rows, (int, float)) and rows >= self._cfg.rows_severe:
                return "high"
        return "medium"
