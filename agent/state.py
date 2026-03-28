"""
agent.state：LangGraph 状态类型定义。

职责：描述 SqlDoctor 流水线在图中的状态字段，供节点间传递。
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class SqlDoctorState(TypedDict, total=False):
    """扩展 MessagesState：在消息列表之外携带原始 SQL 与方言。"""

    messages: Annotated[list[Any], add_messages]
    sql: str
    dialect: str
