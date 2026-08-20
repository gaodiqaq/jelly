"""WebSocket 事件协议：Agent 回调事件 -> 前端 JSON 事件。

前端只消费这一组事件类型（``type`` 字段判别）。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StatusEvent(BaseModel):
    """状态提示（如"正在调用模型…"）。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["status"] = "status"
    message: str


class TokenEvent(BaseModel):
    """流式文本增量。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["token"] = "token"
    text: str


class ToolCallEvent(BaseModel):
    """模型发起一次工具调用。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["tool_call"] = "tool_call"
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultEvent(BaseModel):
    """一次工具执行完成。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["tool_result"] = "tool_result"
    name: str
    content: str
    is_error: bool = False


class MessageEvent(BaseModel):
    """完整文本回复（非流式路径）。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["message"] = "message"
    content: str


class SkillActivatedEvent(BaseModel):
    """Skill 模式已激活。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["skill_activated"] = "skill_activated"
    name: str
    description: str


class ErrorEvent(BaseModel):
    """错误事件（模型调用失败等）。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["error"] = "error"
    message: str


class DoneEvent(BaseModel):
    """本轮任务结束。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["done"] = "done"


class UsageEvent(BaseModel):
    """Token 用量统计（每轮结束时发送）。"""

    model_config = ConfigDict(extra="forbid")
    type: Literal["usage"] = "usage"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    model: str = ""


ServerEvent = Annotated[
    (
        StatusEvent
        | TokenEvent
        | ToolCallEvent
        | ToolResultEvent
        | MessageEvent
        | ErrorEvent
        | DoneEvent
        | UsageEvent
    ),
    Field(discriminator="type"),
]


class ClientMessage(BaseModel):
    """客户端发送的消息。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["user_message"] = "user_message"
    content: str = Field(min_length=1)


class StopMessage(BaseModel):
    """客户端请求停止当前生成。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["stop"] = "stop"


ClientEvent = Annotated[ClientMessage | StopMessage, Field(discriminator="type")]


__all__ = [
    "ServerEvent",
    "StatusEvent",
    "TokenEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "MessageEvent",
    "ErrorEvent",
    "DoneEvent",
    "UsageEvent",
    "ClientMessage",
    "StopMessage",
    "ClientEvent",
]
