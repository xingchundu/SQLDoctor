"""
db.config：数据源连接相关的 Pydantic 模型。

职责：描述业务层可序列化的连接参数，避免在领域代码中散落裸字符串。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class SqlDialect(str, Enum):
    """支持扩展的数据库方言枚举。"""

    MYSQL = "mysql"
    POSTGRES = "postgres"
    ORACLE = "oracle"


class DatabaseConnectionParams(BaseModel):
    """逻辑数据源描述；实际 URL 可由运维映射生成。"""

    dialect: SqlDialect = Field(description="sqlglot / SQLAlchemy 方言")
    database_url: str | None = Field(default=None, description="完整异步连接串")
    schema_name: str | None = Field(default=None, description="Oracle/PG 默认 schema")
