"""
ui.app：Streamlit 对话式 SQL 诊断界面。

职责：支持「工具链分析」与「RAG+LLM 诊断」两种模式；展示执行计划、问题分析、
      优化建议、优化 SQL；会话内多轮记忆。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx
import streamlit as st

from analyzer.plan_analysis_text import format_plan_analysis_sections
from app_exception import AppException

DEFAULT_API = "http://127.0.0.1:8010"

SESSION_TURNS = "sqldoctor_turns"
SESSION_API = "sqldoctor_api_base"
SESSION_DIALECT = "sqldoctor_dialect"
SESSION_USE_RAG = "sqldoctor_use_rag"
SESSION_DB_URL = "sqldoctor_database_url"


def _init_session() -> None:
    if SESSION_TURNS not in st.session_state:
        st.session_state[SESSION_TURNS] = []
    if SESSION_API not in st.session_state:
        st.session_state[SESSION_API] = DEFAULT_API
    if SESSION_DIALECT not in st.session_state:
        st.session_state[SESSION_DIALECT] = "mysql"
    if SESSION_USE_RAG not in st.session_state:
        st.session_state[SESSION_USE_RAG] = False
    if SESSION_DB_URL not in st.session_state:
        st.session_state[SESSION_DB_URL] = ""


async def _post_analysis(api_base: str, sql: str, dialect: str) -> dict[str, Any]:
    url = api_base.rstrip("/") + "/api/analysis/run"
    payload = {"sql": sql, "dialect": dialect}
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise AppException(
                "UI_INVALID_RESPONSE",
                "API 返回非对象 JSON",
                details={"type": type(data).__name__},
            )
        return data


async def _post_rag_diagnose(
    api_base: str,
    sql: str,
    dialect: str,
    database_url: str | None,
) -> dict[str, Any]:
    url = api_base.rstrip("/") + "/api/rag/diagnose"
    payload: dict[str, Any] = {"sql": sql, "dialect": dialect, "analyze": False}
    if database_url and database_url.strip():
        payload["database_url"] = database_url.strip()
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise AppException(
                "UI_INVALID_RESPONSE",
                "RAG API 返回非对象 JSON",
                details={"type": type(data).__name__},
            )
        return data


def _pretty_json(data: Any) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, indent=2)
    except TypeError:
        return str(data)


def _build_summary_tools(result: dict[str, Any]) -> str:
    parse = result.get("parse") or {}
    plan = result.get("plan") or {}
    sugg = result.get("suggestions") or {}
    rew = result.get("rewrite") or {}
    pa = result.get("plan_analysis") or {}
    summary = pa.get("summary") if isinstance(pa, dict) else {}
    n_issues = len(parse.get("issues") or [])
    n_items = len(sugg.get("items") or [])
    n_cand = len(rew.get("candidates") or [])
    if plan.get("skipped"):
        plan_txt = "执行计划未拉取（无库连接或跳过）。"
    else:
        plan_txt = "已获取执行计划结构化结果。"
    plan_rule = ""
    if isinstance(summary, dict) and summary.get("unique_rules") is not None:
        hits = summary.get("total_rule_hits")
        plan_rule = f" · 计划规则 {summary.get('unique_rules')} 种（累计 {hits} 次）"
    return (
        f"**工具链概要** · 解析问题 {n_issues} 条 · {plan_txt}{plan_rule} "
        f"· 规则建议 {n_items} 条 · 改写候选 {n_cand} 个。"
    )


def _build_summary_rag(result: dict[str, Any]) -> str:
    n_rag = len(result.get("rag_chunks") or [])
    n_iss = len(result.get("issues") or [])
    n_sug = len(result.get("suggestions") or [])
    return (
        f"**RAG + LLM 概要** · 知识库命中片段 {n_rag} 条 · "
        f"模型输出问题 {n_iss} 条 · 建议 {n_sug} 条。"
    )


def _render_plan(plan: dict[str, Any] | None) -> None:
    if not plan:
        st.caption("无计划数据。")
        return
    if plan.get("skipped"):
        st.info(plan.get("reason") or "计划步骤已跳过。")
        return
    steps = plan.get("steps")
    if steps and isinstance(steps, list):
        st.markdown("**步骤（统一视图）**")
        try:
            st.dataframe(steps, use_container_width=True, hide_index=True)
        except Exception:
            st.code(_pretty_json(steps), language="json")
    raw_rows = plan.get("raw_rows")
    if raw_rows:
        with st.expander("原始 EXPLAIN 行", expanded=False):
            st.code(_pretty_json(raw_rows), language="json")
    with st.expander("完整 plan JSON", expanded=False):
        st.code(_pretty_json(plan), language="json")


def _render_problem_analysis(
    parse: dict[str, Any] | None,
    plan: dict[str, Any] | None,
    plan_analysis: dict[str, Any] | None = None,
) -> None:
    parse = parse or {}
    issues = parse.get("issues") or []
    if issues:
        st.markdown("**解析 / 静态问题**")
        for it in issues:
            sev = it.get("severity", "info")
            msg = it.get("message", "")
            code = it.get("code", "")
            st.markdown(f"- `{code}` · **{sev}** — {msg}")
    else:
        st.caption("未发现解析层问题条目。")

    st.markdown("**表引用**")
    tables = parse.get("tables_referenced") or []
    st.write(", ".join(tables) if tables else "—")

    pa_md = format_plan_analysis_sections(
        plan_analysis if isinstance(plan_analysis, dict) else None
    )
    if pa_md.strip():
        st.markdown(pa_md)

    st.markdown("**与执行计划相关（逐步启发式）**")
    if not plan or plan.get("skipped"):
        st.caption("无可用计划，无法从计划侧补充分析。")
        return
    if pa_md.strip():
        st.caption("上方 ③ 已基于结构化规则汇总；以下为逐步扫描的补充提示。")
    steps = plan.get("steps") or []
    hints: list[str] = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            continue
        t = (s.get("type") or "").upper()
        k = s.get("key")
        r = s.get("rows")
        ex = str(s.get("extra") or "")
        if "ALL" in t or "SEQ SCAN" in t:
            hints.append(f"步骤 {i}：访问偏全表扫描（{t}），关注谓词与索引。")
        if k in (None, "", "null", "NULL"):
            hints.append(f"步骤 {i}：未显示使用索引（key 为空）。")
        if r is not None:
            try:
                if float(r) > 10_000:
                    hints.append(f"步骤 {i}：估算行数较大（{r}）。")
            except (TypeError, ValueError):
                pass
        if "filesort" in ex.lower():
            hints.append(f"步骤 {i}：存在 filesort（{ex[:80]}）。")
        if "temporary" in ex.lower():
            hints.append(f"步骤 {i}：可能使用临时表（{ex[:80]}）。")
    if hints:
        for h in hints:
            st.markdown(f"- {h}")
    else:
        st.caption("未从计划步骤中匹配到额外启发式提示。")


def _render_suggestions(suggestions: dict[str, Any] | None) -> None:
    if not suggestions:
        st.caption("无建议数据。")
        return
    items = suggestions.get("items") or []
    if not items:
        st.caption("建议列表为空。")
        return
    for it in items:
        title = it.get("title", "")
        detail = it.get("detail", "")
        sev = it.get("severity", "")
        st.markdown(f"**{title}** · `{sev}`")
        st.caption(detail)


def _render_rewrite(rewrite: dict[str, Any] | None) -> None:
    if not rewrite:
        st.caption("无改写数据。")
        return
    cands = rewrite.get("candidates") or []
    if not cands:
        st.caption("无改写候选。")
        return
    for i, c in enumerate(cands):
        title = c.get("title", f"候选 {i + 1}")
        sql_text = c.get("sql_text", "")
        notes = c.get("notes", "")
        st.markdown(f"**{title}**")
        if notes:
            st.caption(notes)
        st.code(sql_text or "", language="sql")


def _render_assistant_tools(result: dict[str, Any] | None, error: str | None) -> None:
    if error:
        st.error(error)
        return
    assert result is not None
    st.markdown(_build_summary_tools(result))
    tab1, tab2, tab3, tab4 = st.tabs(
        ["执行计划", "问题分析", "优化建议", "优化 SQL"],
    )
    with tab1:
        _render_plan(result.get("plan"))
    with tab2:
        _render_problem_analysis(
            result.get("parse"),
            result.get("plan"),
            result.get("plan_analysis"),
        )
    with tab3:
        _render_suggestions(result.get("suggestions"))
    with tab4:
        _render_rewrite(result.get("rewrite"))


def _render_assistant_rag(result: dict[str, Any] | None, error: str | None) -> None:
    if error:
        st.error(error)
        return
    assert result is not None
    st.markdown(_build_summary_rag(result))
    tab1, tab2, tab3, tab4 = st.tabs(
        ["执行计划", "问题分析", "优化建议", "优化 SQL"],
    )
    with tab1:
        _render_plan(result.get("explain"))
    with tab2:
        pa = result.get("plan_analysis") or {}
        st.markdown("**规则分析（基于 EXPLAIN）**")
        pa_md = format_plan_analysis_sections(pa if isinstance(pa, dict) else None)
        if pa_md.strip():
            st.markdown(pa_md)
        else:
            st.caption("无结构化 plan_analysis 摘要（可能未执行 EXPLAIN）。")
        with st.expander("plan_analysis 完整 JSON", expanded=False):
            st.code(_pretty_json(pa), language="json")
        st.markdown("**模型归纳的问题**")
        for i, x in enumerate(result.get("issues") or [], 1):
            st.markdown(f"{i}. {x}")
        if not result.get("issues"):
            st.caption("无 issues 输出。")
    with tab3:
        st.markdown("**模型优化建议**（已结合知识库检索）")
        for i, x in enumerate(result.get("suggestions") or [], 1):
            st.markdown(f"{i}. {x}")
        if not result.get("suggestions"):
            st.caption("无 suggestions 输出。")
        chunks = result.get("rag_chunks") or []
        if chunks:
            with st.expander(f"知识库检索命中（{len(chunks)}）", expanded=False):
                st.code(_pretty_json(chunks), language="json")
    with tab4:
        sql_opt = result.get("optimized_sql") or ""
        st.markdown("**优化后的 SQL**")
        st.code(sql_opt, language="sql")


def _render_assistant_turn(turn: dict[str, Any]) -> None:
    mode = turn.get("mode") or "tools"
    if mode == "rag":
        _render_assistant_rag(turn.get("result"), turn.get("error"))
    else:
        _render_assistant_tools(turn.get("result"), turn.get("error"))


def _dialect_index(current: str) -> int:
    opts = ["mysql", "postgres", "oracle"]
    return opts.index(current) if current in opts else 0


def main() -> None:
    st.set_page_config(page_title="SQL Doctor", layout="wide", initial_sidebar_state="expanded")
    _init_session()
    st.markdown("### SQL Doctor")
    st.caption("多轮对话保存在浏览器本会话内；支持工具链与 RAG+LLM 两种模式。")

    with st.sidebar:
        st.markdown("**连接**")
        api = st.text_input(
            "API Base",
            value=st.session_state[SESSION_API],
            key="api_input_widget",
        )
        st.session_state[SESSION_API] = api
        dialect = st.selectbox(
            "Dialect",
            ["mysql", "postgres", "oracle"],
            index=_dialect_index(st.session_state[SESSION_DIALECT]),
        )
        st.session_state[SESSION_DIALECT] = dialect
        use_rag = st.checkbox(
            "RAG + LLM 诊断（FAISS 知识库 + 大模型）",
            value=st.session_state[SESSION_USE_RAG],
            help="需后端配置 llm_model；首次会下载向量模型/建索引，较慢。",
        )
        st.session_state[SESSION_USE_RAG] = use_rag
        db_url = st.text_input(
            "Database URL（可选，用于 EXPLAIN）",
            value=st.session_state[SESSION_DB_URL],
            type="password",
            help="如 mysql+aiomysql://user:pass@host:3306/db",
        )
        st.session_state[SESSION_DB_URL] = db_url
        if st.button("清空对话记忆", type="secondary"):
            st.session_state[SESSION_TURNS] = []
            st.rerun()
        st.divider()
        st.markdown("**历史 SQL**")
        for idx, t in enumerate(st.session_state[SESSION_TURNS]):
            snippet = (t.get("sql") or "").replace("\n", " ").strip()
            tag = "RAG" if t.get("mode") == "rag" else "工具"
            if len(snippet) > 48:
                snippet = snippet[:48] + "…"
            st.caption(f"{idx + 1}. [{tag}] {snippet or '(空)'}")

    turns: list[dict[str, Any]] = st.session_state[SESSION_TURNS]

    for turn in turns:
        with st.chat_message("user"):
            st.code(turn.get("sql", ""), language="sql")
        with st.chat_message("assistant"):
            _render_assistant_turn(turn)

    prompt = st.chat_input("输入要分析的 SQL…")
    if prompt and prompt.strip():
        sql_text = prompt.strip()
        mode = "rag" if st.session_state[SESSION_USE_RAG] else "tools"
        with st.spinner("分析中…"):
            try:
                if mode == "rag":
                    data = asyncio.run(
                        _post_rag_diagnose(
                            st.session_state[SESSION_API],
                            sql_text,
                            st.session_state[SESSION_DIALECT],
                            st.session_state[SESSION_DB_URL] or None,
                        )
                    )
                else:
                    data = asyncio.run(
                        _post_analysis(
                            st.session_state[SESSION_API],
                            sql_text,
                            st.session_state[SESSION_DIALECT],
                        )
                    )
                turns.append(
                    {"sql": sql_text, "result": data, "error": None, "mode": mode}
                )
            except AppException as exc:
                turns.append(
                    {
                        "sql": sql_text,
                        "result": None,
                        "error": f"{exc.message} ({exc.code})",
                        "mode": mode,
                    }
                )
            except Exception as exc:
                turns.append(
                    {
                        "sql": sql_text,
                        "result": None,
                        "error": str(exc),
                        "mode": mode,
                    }
                )
        st.session_state[SESSION_TURNS] = turns
        st.rerun()


main()
