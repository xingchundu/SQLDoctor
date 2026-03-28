"""
跨模块共享的应用层异常定义。

职责：为 db / analyzer / optimizer / agent / backend 提供统一异常基类，
避免业务包之间相互导入导致的循环依赖。
"""

from __future__ import annotations

from typing import Any


class AppException(Exception):
    """应用统一异常基类；禁止在业务路径中裸 raise 内置 Exception。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code: str = code
        self.message: str = message
        self.details: dict[str, Any] = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class ConfigurationError(AppException):
    """配置缺失或非法。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__("CONFIG_ERROR", message, details=details)


class DatabaseError(AppException):
    """数据库访问、连接池或查询执行失败。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__("DATABASE_ERROR", message, details=details)


class ParseError(AppException):
    """SQL 解析或 AST 处理失败。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__("PARSE_ERROR", message, details=details)


class PlanAnalysisError(AppException):
    """执行计划获取或解析失败。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__("PLAN_ERROR", message, details=details)


class OptimizerError(AppException):
    """优化建议或改写流程失败。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__("OPTIMIZER_ERROR", message, details=details)


class AgentError(AppException):
    """LangGraph / 工具编排失败。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__("AGENT_ERROR", message, details=details)


class CacheError(AppException):
    """Redis 或缓存访问失败。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__("CACHE_ERROR", message, details=details)
