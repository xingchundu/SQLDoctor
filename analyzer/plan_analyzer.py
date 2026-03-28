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
    """单条识别到的问题（含原因，便于前端与 API 序列化）。"""

    code: str = Field(description="稳定机器可读码")
    title: str = Field(description="短标题")
    reason: str = Field(description="为何构成问题及可能影响")
    step_index: int | None = Field(default=None, description="对应计划步骤下标，无则 null")
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="触发该条的字段快照，如 type/key/rows/extra",
    )


class PlanAnalysisReport(BaseModel):
    """计划分析 JSON 结果（与需求字段对齐）。"""

    problems: list[PlanProblemItem] = Field(default_factory=list)
    risk_level: RiskLevel = "low"
    details: list[str] = Field(
        default_factory=list,
        description="汇总性说明，便于阅读",
    )

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "problems": [p.model_dump() for p in self.problems],
            "risk_level": self.risk_level,
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


def _has_filesort(step: dict[str, Any]) -> bool:
    ex = _extra_text(step).lower()
    if "using filesort" in ex:
        return True
    typ = _norm_str(step.get("type")).lower()
    if "sort" in typ and "index" not in typ:
        return True
    return False


def _has_temporary(step: dict[str, Any]) -> bool:
    ex = _extra_text(step).lower()
    return "using temporary" in ex


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
                details=["未解析到任何计划步骤（steps 为空或格式不匹配），无法做规则检测。"],
            )

        problems: list[PlanProblemItem] = []
        for idx, step in enumerate(steps):
            problems.extend(self._scan_step(idx, step))

        risk = self._compute_risk_level(problems)
        details = self._build_details(steps, problems, risk)
        return PlanAnalysisReport(problems=problems, risk_level=risk, details=details)

    def _scan_step(self, idx: int, step: dict[str, Any]) -> list[PlanProblemItem]:
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
                    step_index=idx,
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
                    step_index=idx,
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
                    step_index=idx,
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
                    step_index=idx,
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
                    step_index=idx,
                    evidence=evidence_base,
                )
            )

        return found

    def _compute_risk_level(self, problems: list[PlanProblemItem]) -> RiskLevel:
        if not problems:
            return "low"

        codes = {p.code for p in problems}
        if "USING_FILESORT" in codes and "USING_TEMPORARY" in codes:
            return "high"
        if "FULL_TABLE_SCAN" in codes and "ROWS_TOO_LARGE" in codes:
            return "high"
        if len(problems) >= 4:
            return "high"
        if len(problems) >= 2:
            return "medium"
        if problems[0].code == "ROWS_TOO_LARGE":
            rows = problems[0].evidence.get("rows_numeric")
            if isinstance(rows, (int, float)) and rows >= self._cfg.rows_severe:
                return "high"
        return "medium"

    def _build_details(
        self,
        steps: list[dict[str, Any]],
        problems: list[PlanProblemItem],
        risk: RiskLevel,
    ) -> list[str]:
        lines: list[str] = [
            f"共分析 {len(steps)} 个计划步骤，识别到 {len(problems)} 条问题项。",
            f"综合风险等级：{risk}（high 表示存在叠加或高危组合，建议优先排查）。",
        ]
        if problems:
            by_code: dict[str, int] = {}
            for p in problems:
                by_code[p.code] = by_code.get(p.code, 0) + 1
            summary = "；".join(f"{k}×{v}" for k, v in sorted(by_code.items()))
            lines.append(f"问题类型分布：{summary}。")
        return lines
