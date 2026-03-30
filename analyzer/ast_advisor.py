"""
analyzer.ast_advisor：基于 sqlglot AST 的静态规则建议（与 EXPLAIN 无关）。

职责：补充 ParseIssue；规则参考常见 SQL 反模式（SELECT *、缺失 WHERE、OR、NULL 比较等）。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError as SqlglotParseError

from analyzer.models import ParseIssue


@dataclass(frozen=True)
class AstAdviceHit:
    level: str  # ERROR / WARNING / INFO
    rule: str
    message: str
    snippet: str = ""


def hits_to_parse_issues(hits: list[AstAdviceHit]) -> list[ParseIssue]:
    out: list[ParseIssue] = []
    for h in hits:
        sev = {"ERROR": "error", "WARNING": "warning", "INFO": "info"}.get(
            h.level.upper(), "info"
        )
        out.append(
            ParseIssue(
                severity=sev,
                code=h.rule,
                message=h.message if not h.snippet else f"{h.message}（片段：{h.snippet}）",
            )
        )
    return out


def _select_inside_subquery_context(select: exp.Select) -> bool:
    """子查询 / EXISTS / IN 列表内的 SELECT 不强制要求 WHERE。"""
    p = select.parent
    while p is not None:
        if isinstance(p, (exp.Subquery, exp.Exists)):
            return True
        if isinstance(p, exp.In) and select is not p.this:
            return True
        if isinstance(p, exp.CTE):
            return True
        p = getattr(p, "parent", None)
    return False


class SqlAstOptimizationAdvisor:
    """基于 AST 的静态 SQL 优化建议引擎。"""

    def analyze(self, sql: str, dialect: str) -> list[AstAdviceHit]:
        try:
            tree = parse_one(sql, dialect=dialect)
        except SqlglotParseError as e:
            return [
                AstAdviceHit("ERROR", "PARSE_ERROR", f"sqlglot 解析失败：{e}", sql[:120])
            ]
        except Exception as e:
            return [AstAdviceHit("ERROR", "PARSE_ERROR", str(e), sql[:120])]
        return self.analyze_tree(tree)

    def analyze_tree(self, tree: exp.Expression) -> list[AstAdviceHit]:
        suggestions: list[AstAdviceHit] = []
        suggestions.extend(self._check_select_star(tree))
        suggestions.extend(self._check_no_where(tree))
        suggestions.extend(self._check_or_conditions(tree))
        suggestions.extend(self._check_null_comparison(tree))
        suggestions.extend(self._check_functions_on_columns(tree))
        suggestions.extend(self._check_subquery_in_select(tree))
        suggestions.extend(self._check_implicit_type_cast(tree))
        suggestions.extend(self._check_like_leading_wildcard(tree))
        suggestions.extend(self._check_distinct_overuse(tree))
        suggestions.extend(self._check_not_in_with_null(tree))
        return self._dedupe_by_rule(suggestions)

    def _dedupe_by_rule(self, hits: list[AstAdviceHit]) -> list[AstAdviceHit]:
        seen: set[str] = set()
        out: list[AstAdviceHit] = []
        for h in hits:
            if h.rule in seen:
                continue
            seen.add(h.rule)
            out.append(h)
        return out

    def _check_select_star(self, tree: exp.Expression) -> list[AstAdviceHit]:
        results: list[AstAdviceHit] = []
        for select in tree.find_all(exp.Select):
            if _select_inside_subquery_context(select):
                continue
            for col in select.expressions:
                if isinstance(col, exp.Star):
                    results.append(
                        AstAdviceHit(
                            "WARNING",
                            "NO_SELECT_STAR",
                            "避免使用 SELECT *，应明确列出需要的字段，减少网络传输和内存消耗",
                            "SELECT *",
                        )
                    )
                    break
        return results

    def _check_no_where(self, tree: exp.Expression) -> list[AstAdviceHit]:
        results: list[AstAdviceHit] = []
        for node in tree.find_all((exp.Select, exp.Update, exp.Delete)):
            if isinstance(node, exp.Select) and _select_inside_subquery_context(node):
                continue
            if node.find(exp.Where) is None:
                op = type(node).__name__.upper()
                snippet = node.sql()[:120]
                level = (
                    "ERROR"
                    if isinstance(node, (exp.Update, exp.Delete))
                    else "WARNING"
                )
                results.append(
                    AstAdviceHit(
                        level,
                        "MISSING_WHERE",
                        f"{op} 缺少 WHERE 条件，可能导致全表扫描或误更新/删除",
                        snippet,
                    )
                )
        return results

    def _check_or_conditions(self, tree: exp.Expression) -> list[AstAdviceHit]:
        results: list[AstAdviceHit] = []
        for where in tree.find_all(exp.Where):
            if where.find(exp.Or):
                results.append(
                    AstAdviceHit(
                        "WARNING",
                        "OR_CONDITION",
                        "WHERE 中存在 OR，可能导致索引利用不佳，可考虑 UNION ALL 等改写",
                        where.sql()[:120],
                    )
                )
        return results

    def _check_null_comparison(self, tree: exp.Expression) -> list[AstAdviceHit]:
        results: list[AstAdviceHit] = []
        for eq in tree.find_all(exp.EQ):
            if isinstance(eq.right, exp.Null) or isinstance(eq.left, exp.Null):
                results.append(
                    AstAdviceHit(
                        "ERROR",
                        "NULL_COMPARISON",
                        "不要用 = NULL 判断空值，应使用 IS NULL 或 IS NOT NULL",
                        eq.sql()[:120],
                    )
                )
        return results

    def _check_functions_on_columns(self, tree: exp.Expression) -> list[AstAdviceHit]:
        results: list[AstAdviceHit] = []
        for where in tree.find_all(exp.Where):
            warned = False
            for func in where.find_all(exp.Func):
                if func.find(exp.Column):
                    results.append(
                        AstAdviceHit(
                            "WARNING",
                            "FUNC_ON_INDEXED_COLUMN",
                            "WHERE 中对列使用函数可能导致索引失效，尽量对常量侧做变换",
                            func.sql()[:120],
                        )
                    )
                    warned = True
                    break
            if warned:
                continue
            for anon in where.find_all(exp.Anonymous):
                if anon.find(exp.Column):
                    results.append(
                        AstAdviceHit(
                            "WARNING",
                            "FUNC_ON_INDEXED_COLUMN",
                            "WHERE 中对列使用函数或运算可能导致索引失效",
                            anon.sql()[:120],
                        )
                    )
                    break
        return results

    def _check_subquery_in_select(self, tree: exp.Expression) -> list[AstAdviceHit]:
        results: list[AstAdviceHit] = []
        for select in tree.find_all(exp.Select):
            for expr in select.expressions:
                if expr.find(exp.Subquery):
                    results.append(
                        AstAdviceHit(
                            "WARNING",
                            "SUBQUERY_IN_SELECT",
                            "SELECT 列表中的标量子查询可能形成 N+1，建议改为 JOIN",
                            expr.sql()[:120],
                        )
                    )
        return results

    def _check_like_leading_wildcard(self, tree: exp.Expression) -> list[AstAdviceHit]:
        results: list[AstAdviceHit] = []
        for like in tree.find_all(exp.Like):
            pattern = like.expression
            if isinstance(pattern, exp.Literal):
                val = str(pattern.this if hasattr(pattern, "this") else pattern.name)
                if val.startswith("%"):
                    results.append(
                        AstAdviceHit(
                            "WARNING",
                            "LIKE_LEADING_WILDCARD",
                            f"LIKE 以 % 开头难以利用普通 B-Tree 索引，可考虑全文检索或约束前缀",
                            like.sql()[:120],
                        )
                    )
        return results

    def _check_distinct_overuse(self, tree: exp.Expression) -> list[AstAdviceHit]:
        results: list[AstAdviceHit] = []
        for select in tree.find_all(exp.Select):
            if select.args.get("distinct") and select.find(exp.Join):
                results.append(
                    AstAdviceHit(
                        "INFO",
                        "DISTINCT_WITH_JOIN",
                        "带 JOIN 的 DISTINCT 可能由 JOIN 条件引起重复，请确认关联逻辑",
                        select.sql()[:120],
                    )
                )
        return results

    def _check_implicit_type_cast(self, tree: exp.Expression) -> list[AstAdviceHit]:
        results: list[AstAdviceHit] = []
        for cmp in tree.find_all((exp.EQ, exp.GT, exp.LT, exp.GTE, exp.LTE)):
            left, right = cmp.left, cmp.right
            if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
                if right.is_string and str(right.this).strip("'\"").isdigit():
                    results.append(
                        AstAdviceHit(
                            "WARNING",
                            "IMPLICIT_TYPE_CAST",
                            f"列 [{left.sql()}] 与字符串数字比较可能触发隐式类型转换",
                            cmp.sql()[:120],
                        )
                    )
        return results

    def _check_not_in_with_null(self, tree: exp.Expression) -> list[AstAdviceHit]:
        results: list[AstAdviceHit] = []
        for not_node in tree.find_all(exp.Not):
            if isinstance(not_node.this, exp.In):
                results.append(
                    AstAdviceHit(
                        "WARNING",
                        "NOT_IN_NULL_RISK",
                        "NOT IN（含子查询）若结果含 NULL 会使整体为假，可考虑 NOT EXISTS",
                        not_node.sql()[:120],
                    )
                )
        return results
