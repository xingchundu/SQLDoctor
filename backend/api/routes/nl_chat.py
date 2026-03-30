"""
backend.api.routes.nl_chat：中文/自然语言对话（不执行 EXPLAIN），走本地 Ollama 等。

职责：与「SQL 诊断」分流；仅本路由 + 前端分支，其它模块不变。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agent.sql_agent import _message_content_to_text, build_chat_openai_from_settings
from db.config import SqlDialect

router = APIRouter()

_NL_SYSTEM = """你是 SQL Doctor 助手。用户可能用中文讨论数据库、SQL 写法、索引、执行计划或优化思路。
请直接用自然语言回答，不要使用 JSON 或代码围栏包裹整段回复（讲解时如需示例 SQL 可用小段代码）。
回答简洁、可执行；若需要对方提供具体 SQL，请明确说明。

你必须严格按「当前界面选择的数据库引擎」作答：示例 SQL、EXPLAIN/执行计划用语、索引与优化习惯均须与该引擎一致；
不要默认按 MySQL 回答，除非用户明确只讨论 MySQL。"""


class NlChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class NlChatRequest(BaseModel):
    messages: list[NlChatMessage] = Field(
        min_length=1,
        description="完整对话（含本轮用户消息），角色交替",
    )
    dialect: str = Field(default="mysql", description="当前界面选择的数据库方言，供语境说明")


class NlChatResponse(BaseModel):
    reply: str


def _nl_engine_line(dialect: SqlDialect) -> str:
    names = {
        SqlDialect.MYSQL: "MySQL / MariaDB",
        SqlDialect.POSTGRES: "PostgreSQL",
        SqlDialect.ORACLE: "Oracle",
    }
    return (
        f"【当前界面选择的数据库】{names.get(dialect, dialect.value)}（dialect={dialect.value}）。\n"
        "所有优化建议、语法示例与执行计划解释均须针对该引擎；若涉及 EXPLAIN，请按该引擎的典型输出形态说明。"
    )


@router.post("/nl-chat", response_model=NlChatResponse)
async def natural_language_chat(body: NlChatRequest) -> NlChatResponse:
    llm = build_chat_openai_from_settings()
    try:
        sd = SqlDialect(body.dialect)
    except ValueError:
        sd = SqlDialect.MYSQL
    sys = f"{_NL_SYSTEM}\n\n{_nl_engine_line(sd)}"
    lc_messages: list = [SystemMessage(content=sys)]
    for m in body.messages[-40:]:
        if m.role == "user":
            lc_messages.append(HumanMessage(content=m.content))
        else:
            lc_messages.append(AIMessage(content=m.content))
    resp = await llm.ainvoke(lc_messages)
    text = _message_content_to_text(resp.content).strip()
    return NlChatResponse(reply=text or "（模型未返回内容）")
