"""
analyzer 包：基于 sqlglot 的 SQL 解析与执行计划采集、结构化。

职责：把原始 SQL 与 EXPLAIN 输出转为稳定的 Pydantic 模型，供 optimizer 与 agent 消费。
"""
