"""agent-shell 核心异常类型。

所有异常都继承自 :class:`AgentError`，错误信息为面向终端用户的
中文结构化描述；底层异常通过 ``raise ... from exc`` 保留链，便于调试。
各层禁止直接 ``sys.exit()``，一律向上抛出异常或返回结构化结果。
"""

from __future__ import annotations

__all__ = [
    "AgentError",
    "ConfigError",
    "LLMError",
    "SessionError",
    "AgentInterrupted",
]


class AgentError(Exception):
    """所有 agent-shell 异常的基类。"""


class ConfigError(AgentError):
    """配置加载或校验失败（文件缺失、YAML 语法错误、字段非法等）。"""


class LLMError(AgentError):
    """大模型 API 调用失败。

    Attributes:
        retryable: 是否为可重试错误（网络抖动、限流等）。
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class SessionError(AgentError):
    """会话持久化失败（写入/读取/恢复会话文件出错）。"""


class AgentInterrupted(AgentError):
    """用户主动中断（Ctrl+C 或 /stop 命令），需要干净地停止当前循环。"""
