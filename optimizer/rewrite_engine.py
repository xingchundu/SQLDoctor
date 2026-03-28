"""
optimizer.rewrite_engine：基于 sqlglot 的规则化 SQL 改写（结合问题列表可选触发）。

职责：在可验证前提下做 SELECT * 展开、LIMIT 收敛、隐式逗号连接向 INNER JOIN 提升、
      以及索引建议注释；输出单一「优化后的 SQL」字符串。
"""

from __future__ import annotations

import asyncio
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError as SqlglotParseError

from app_exception import OptimizerError, ParseError
from db.config import SqlDialect
from pydantic import BaseModel, Field


class RewriteEngineOptions(BaseModel):
    """改写引擎可选参数。"""

    model_config = {"extra": "forbid"}

    table_columns: dict[str, list[str]] = Field(
        default_factory=dict,
        description="表名/别名 -> 列名列表，用于 SELECT * 展开；键可为 t 或 schema.t",
    )
    max_limit: int | None = Field(
        default=100_000,
        ge=1,
        description="LIMIT 超过该值时截断到此上限；None 表示不截断",
    )
    safety_limit_if_missing: int | None = Field(
        default=None,
        ge=1,
        description="顶层 SELECT 无 LIMIT 时追加 LIMIT（慎用，仅适合即席查询场景）",
    )
    issue_codes: list[str] = Field(
        default_factory=list,
        description="如 FULL_TABLE_SCAN、ROWS_TOO_LARGE；用于决定是否追加安全 LIMIT",
    )
    append_index_suggestions: bool = Field(
        default=True,
        description="是否在 SQL 末尾追加索引建议注释块",
    )
    enable_comma_join_lift: bool = Field(
        default=True,
        description="是否尝试将 FROM a, b WHERE a.x=b.x 提升为 INNER JOIN",
    )


def _dialect_str(d: SqlDialect) -> str:
    return d.value


def _parse(sql: str, dialect: SqlDialect) -> exp.Expression:
    try:
        return sqlglot.parse_one(sql, read=_dialect_str(dialect))
    except SqlglotParseError as exc:
        raise ParseError("改写引擎解析失败", details={"reason": str(exc)}) from exc


def _table_lookup_keys(table: exp.Table) -> list[str]:
    name = table.name
    db = table.db
    keys = [name]
    if db:
        keys.append(f"{db}.{name}")
    return keys


def _resolve_columns_for_table(table: exp.Table, catalog: dict[str, list[str]]) -> list[str] | None:
    for k in _table_lookup_keys(table):
        if k in catalog:
            return list(catalog[k])
    return None


def _table_in_direct_from_scope(frm: exp.From, table: exp.Table) -> bool:
    """判断表是否属于 FROM 直接子树（不深入子查询内的表）。"""
    cur: exp.Expression | None = table.parent
    while cur is not None:
        if cur is frm:
            return True
        if isinstance(cur, exp.Subquery):
            return False
        cur = getattr(cur, "parent", None)
    return False


def _tables_directly_in_from(select: exp.Select) -> list[exp.Table]:
    frm = select.args.get("from")
    if not frm:
        return []
    candidates = list(frm.find_all(exp.Table))
    return [t for t in candidates if _table_in_direct_from_scope(frm, t)]


def _expand_select_stars(expression: exp.Expression, catalog: dict[str, list[str]]) -> None:
    if not catalog:
        return
    for select in expression.find_all(exp.Select):
        exprs = list(select.expressions)
        if len(exprs) != 1 or not isinstance(exprs[0], exp.Star):
            continue
        if exprs[0].except_ or exprs[0].replace:
            continue
        tables = _tables_directly_in_from(select)
        if len(tables) != 1:
            continue
        cols = _resolve_columns_for_table(tables[0], catalog)
        if not cols:
            continue
        new_cols = [exp.Column(this=exp.to_identifier(c)) for c in cols]
        select.set("expressions", new_cols)


def _limit_value(node: exp.Expression | None) -> int | None:
    if node is None:
        return None
    if isinstance(node, exp.Limit):
        return _literal_int(node.this)
    return _literal_int(node)


def _literal_int(n: exp.Expression | None) -> int | None:
    if n is None:
        return None
    if isinstance(n, exp.Literal):
        try:
            return int(n.this)
        except (TypeError, ValueError):
            return None
    return None


def _apply_limit_rules(
    select: exp.Select,
    *,
    max_limit: int | None,
    safety_limit_if_missing: int | None,
    issue_codes: list[str],
) -> None:
    lim_node = select.args.get("limit")
    current = _limit_value(lim_node)

    if current is not None and max_limit is not None and current > max_limit:
        if isinstance(lim_node, exp.Limit):
            lim_node.set("this", exp.Literal.number(str(max_limit)))
        else:
            select.set("limit", exp.Limit(this=exp.Literal.number(str(max_limit))))

    want_safety = safety_limit_if_missing is not None and select.args.get("limit") is None
    if want_safety:
        triggers = {"FULL_TABLE_SCAN", "ROWS_TOO_LARGE", "INDEX_NOT_USED"}
        if not issue_codes or triggers.intersection(set(issue_codes)):
            select.set(
                "limit",
                exp.Limit(this=exp.Literal.number(str(safety_limit_if_missing))),
            )


def _is_comma_style_join(join: exp.Join) -> bool:
    if join.args.get("on") is not None or join.args.get("using") is not None:
        return False
    kind = join.args.get("kind")
    if kind is None:
        return True
    s = str(kind).upper()
    return s in ("", ",", "CROSS")


def _collect_join_tables(join: exp.Join) -> list[exp.Table]:
    tables: list[exp.Table] = []

    def walk(node: exp.Expression | None) -> None:
        if node is None:
            return
        if isinstance(node, exp.Table):
            tables.append(node)
            return
        if isinstance(node, exp.Join):
            walk(node.this)
            walk(node.expression)

    walk(join.this)
    walk(join.expression)
    return tables


def _split_conjuncts(node: exp.Expression | None) -> list[exp.Expression]:
    if node is None:
        return []
    if isinstance(node, exp.And):
        return _split_conjuncts(node.left) + _split_conjuncts(node.right)
    return [node]


def _column_table_name(col: exp.Column) -> str | None:
    t = col.table
    return str(t) if t else None


def _try_lift_comma_join(select: exp.Select) -> None:
    frm = select.args.get("from")
    where = select.args.get("where")
    if not frm or not where or not isinstance(frm.this, exp.Join):
        return
    root = frm.this
    if not isinstance(root, exp.Join) or not _is_comma_style_join(root):
        return
    tables = _collect_join_tables(root)
    if len(tables) != 2:
        return
    preds = _split_conjuncts(where.this)
    pair: tuple[exp.Column, exp.Column] | None = None
    rest: list[exp.Expression] = []
    for p in preds:
        if isinstance(p, exp.EQ) and isinstance(p.left, exp.Column) and isinstance(p.right, exp.Column):
            if pair is None:
                pair = (p.left, p.right)
                continue
        rest.append(p)
    if pair is None:
        return
    c1, c2 = pair
    tnames = {tables[0].alias_or_name, tables[1].alias_or_name}
    n1 = _column_table_name(c1)
    n2 = _column_table_name(c2)
    if not n1 or not n2 or n1 == n2:
        return
    if {n1, n2} != tnames:
        return
    left_t, right_t = (tables[0], tables[1]) if tables[0].alias_or_name == n1 else (tables[1], tables[0])
    on_cond = exp.EQ(this=c1, expression=c2)
    new_join = exp.Join(
        this=left_t,
        kind="INNER",
        expression=right_t,
        on=on_cond,
    )
    frm.set("this", new_join)
    if rest:
        new_where = rest[0]
        for r in rest[1:]:
            new_where = exp.And(this=new_where, expression=r)
        select.set("where", exp.Where(this=new_where))
    else:
        select.set("where", None)


def _eq_in_filter_context(eq: exp.Expression) -> bool:
    cur = eq.parent
    while cur is not None:
        if isinstance(cur, (exp.Where, exp.Having, exp.Join)):
            return True
        cur = getattr(cur, "parent", None)
    return False


def _collect_index_suggestion_lines(expression: exp.Expression, dialect: SqlDialect) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for eq in expression.find_all(exp.EQ):
        if not _eq_in_filter_context(eq):
            continue
        for col, side in ((eq.left, eq.right), (eq.right, eq.left)):
            if not isinstance(col, exp.Column):
                continue
            if not isinstance(side, (exp.Literal, exp.Column)):
                continue
            tbl = col.table or ""
            name = col.name
            if not name:
                continue
            idx_key = f"{tbl}.{name}"
            if idx_key in seen:
                continue
            seen.add(idx_key)
            table_sql = str(tbl) if tbl else "table"
            lines.append(
                f"/* INDEX_SUGGESTION: CREATE INDEX idx_{table_sql}_{name} "
                f"ON {table_sql} ({name}); -- 结合谓词与数据分布评估 */"
            )
    if dialect == SqlDialect.POSTGRES:
        lines.append("/* INDEX_SUGGESTION(PG): 可使用 CONCURRENTLY 在线建索引以降锁 */")
    return lines


def _unwrap_outer_select(node: exp.Expression) -> exp.Select:
    if isinstance(node, exp.Select):
        return node
    if isinstance(node, exp.With) and isinstance(node.this, exp.Select):
        return node.this
    raise OptimizerError(
        "当前改写引擎仅处理 SELECT / WITH ... SELECT",
        details={"root": type(node).__name__},
    )


def _apply_rewrite_pipeline(
    sql: str,
    dialect: SqlDialect,
    options: RewriteEngineOptions,
) -> str:
    root = _parse(sql, dialect)
    select_root = _unwrap_outer_select(root)

    _expand_select_stars(root, options.table_columns)
    if options.enable_comma_join_lift:
        _try_lift_comma_join(select_root)
    _apply_limit_rules(
        select_root,
        max_limit=options.max_limit,
        safety_limit_if_missing=options.safety_limit_if_missing,
        issue_codes=options.issue_codes,
    )

    out = root.sql(dialect=_dialect_str(dialect), pretty=True)
    if options.append_index_suggestions:
        idx_lines = _collect_index_suggestion_lines(select_root, dialect)
        if idx_lines:
            out = out.rstrip() + "\n" + "\n".join(idx_lines)
    return out


class SqlRewriteEngine:
    """
    SQL 改写引擎：根据可选「问题码」与列目录等自动优化 SQL，返回优化后的 SQL 字符串。
    """

    def __init__(self, default_options: RewriteEngineOptions | None = None) -> None:
        self._defaults = default_options or RewriteEngineOptions()

    async def rewrite(
        self,
        sql: str,
        dialect: SqlDialect,
        *,
        options: RewriteEngineOptions | None = None,
        issues: list[str] | None = None,
    ) -> str:
        """
        根据规则与可选问题描述优化 SQL。

        issues: 人可读问题句或 code 列表，会并入 issue_codes 用于触发安全 LIMIT 等策略。
        """
        merged = self._defaults.model_copy(update=options.model_dump() if options else {})
        codes = list(merged.issue_codes)
        if issues:
            for s in issues:
                u = s.upper()
                for token in (
                    "FULL_TABLE_SCAN",
                    "ROWS_TOO_LARGE",
                    "INDEX_NOT_USED",
                    "USING_FILESORT",
                    "USING_TEMPORARY",
                ):
                    if token in u or token in s:
                        codes.append(token)
        merged = merged.model_copy(update={"issue_codes": list(dict.fromkeys(codes))})

        try:
            return await asyncio.to_thread(_apply_rewrite_pipeline, sql, dialect, merged)
        except (ParseError, OptimizerError):
            raise
        except Exception as exc:
            raise OptimizerError(
                "改写引擎执行失败",
                details={"reason": str(exc)},
            ) from exc
