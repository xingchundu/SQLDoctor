"""
agent 包：LangGraph 编排、@tool 工具注册与对话/流水线状态。

职责：将 analyzer / optimizer / db 能力封装为可被 ToolNode 调度的工具，并提供可编译的 StateGraph。
"""
