"""
agent.graph：编译 LangGraph StateGraph，使用 ToolNode 调度 @tool 工具。

职责：提供确定性流水线（无 LLM 亦可用），并暴露异步 ainvoke 入口。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from agent.runtime import (
    ToolRuntimeDeps,
    bind_agent_context,
    unbind_agent_context,
)
from agent.state import SqlDoctorState
from agent.tools import all_sql_doctor_tools
from app_exception import AgentError

_TOOL_ORDER: tuple[str, ...] = (
    "parse_sql",
    "fetch_execution_plan",
    "generate_suggestions",
    "rewrite_sql",
)


def _completed_tool_names(messages: list[Any]) -> set[str]:
    done: set[str] = set()
    for m in messages:
        if isinstance(m, ToolMessage) and m.name:
            done.add(m.name)
    return done


def _next_tool_call(sql: str, dialect: str, messages: list[Any]) -> dict | None:
    done = _completed_tool_names(messages)
    for name in _TOOL_ORDER:
        if name in done:
            continue
        if name == "parse_sql":
            args: dict[str, Any] = {"sql": sql, "dialect": dialect}
        elif name == "fetch_execution_plan":
            args = {"sql": sql, "dialect": dialect, "analyze": True}
        elif name == "generate_suggestions":
            args = {}
        elif name == "rewrite_sql":
            args = {"sql": sql, "dialect": dialect}
        else:
            continue
        call_id = f"{name}_{len(messages)}"
        tool_call = {
            "name": name,
            "args": args,
            "id": call_id,
            "type": "tool_call",
        }
        return {"messages": [AIMessage(content="", tool_calls=[tool_call])]}
    return None


async def schedule_tool_calls_node(state: SqlDoctorState) -> dict[str, Any]:
    """按固定顺序下发 tool_calls，直到全部完成。"""
    messages = state.get("messages") or []
    sql = state.get("sql") or ""
    dialect = state.get("dialect") or "mysql"

    nxt = _next_tool_call(sql, dialect, messages)
    if nxt is not None:
        return nxt
    return {"messages": [AIMessage(content="SQL Doctor 流水线执行完毕")]}


def _build_graph() -> Any:
    graph = StateGraph(SqlDoctorState)
    graph.add_node("agent", schedule_tool_calls_node)
    graph.add_node("tools", ToolNode(all_sql_doctor_tools()))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", END: END},
    )
    graph.add_edge("tools", "agent")
    return graph.compile()


_COMPILED_GRAPH: Any | None = None


def get_compiled_sql_doctor_graph() -> Any:
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is None:
        _COMPILED_GRAPH = _build_graph()
    return _COMPILED_GRAPH


class SqlDoctorAgent:
    """对外暴露的异步编排入口。"""

    async def run_pipeline(
        self,
        sql: str,
        dialect: str,
        *,
        runtime: ToolRuntimeDeps,
    ) -> SqlDoctorState:
        binding = bind_agent_context(runtime)
        try:
            graph = get_compiled_sql_doctor_graph()
            initial: SqlDoctorState = {
                "sql": sql,
                "dialect": dialect,
                "messages": [],
            }
            result = await graph.ainvoke(initial)
            if not isinstance(result, dict):
                raise AgentError(
                    "LangGraph 返回类型异常",
                    details={"type": type(result).__name__},
                )
            return result  # type: ignore[return-value]
        finally:
            unbind_agent_context(binding)
