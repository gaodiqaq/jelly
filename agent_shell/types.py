"""跨层共享的数据类型（pydantic 模型）。

``ui/``、``tools/``、``llm/``、``core/`` 四个层级之间只通过这些类型
传递数据，禁止任何层级直接依赖其他层级的实现。

Messages 采用带 ``role`` 判别字段的联合类型，对应 OpenAI 消息协议：
- SystemMessage / UserMessage: 普通文本消息
- AssistantMessage: 模型回复，可携带工具调用（tool_calls）
- ToolMessage: 工具执行结果，通过 tool_call_id 回填
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Message",
    "SystemMessage",
    "UserMessage",
    "AssistantMessage",
    "ToolMessage",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "PermissionDecision",
]


class ToolCall(BaseModel):
    """模型发起的一次工具调用请求。

    Attributes:
        id: 工具调用唯一标识（由模型生成，用于回填 ToolMessage.tool_call_id）。
        name: 工具名称。
        arguments: 工具参数（已解析为 Python 对象）。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

    def format_arguments(self, indent: int = 2) -> str:
        """将参数序列化为 JSON 字符串，用于渲染与回传。

        Args:
            indent: JSON 缩进空格数；``indent=None`` 或 ``0`` 时输出紧凑格式
                （OpenAI 协议要求紧凑格式）。

        Returns:
            参数 JSON 字符串；空参数时返回 ``{}``。
        """
        if indent:
            return json.dumps(self.arguments, ensure_ascii=False, indent=indent)
        return json.dumps(self.arguments, ensure_ascii=False, separators=(",", ":"))


class SystemMessage(BaseModel):
    """系统提示词消息。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["system"] = "system"
    content: str


class UserMessage(BaseModel):
    """用户输入消息。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user"] = "user"
    content: str


class AssistantMessage(BaseModel):
    """模型回复消息，可同时携带文本与工具调用。

    Attributes:
        content: 文本回复，可能为 None（纯工具调用场景）。
        tool_calls: 模型请求执行的工具调用列表。
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ToolMessage(BaseModel):
    """工具执行结果消息。

    Attributes:
        tool_call_id: 回填对应的 ToolCall.id。
        name: 工具名称。
        content: 工具输出文本。
        is_error: 执行是否失败（失败时 content 为结构化错误信息）。
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal["tool"] = "tool"
    tool_call_id: str
    name: str
    content: str
    is_error: bool = False


Message = Annotated[
    SystemMessage | UserMessage | AssistantMessage | ToolMessage,
    Field(discriminator="role"),
]


class ToolResult(BaseModel):
    """工具执行的返回结果。

    Attributes:
        content: 工具输出文本（成功时为输出内容，失败时为错误说明）。
        is_error: 执行是否失败。
    """

    content: str
    is_error: bool = False


class ToolSpec(BaseModel):
    """工具的声明元数据，用于生成 OpenAI 格式的 function schema。

    Attributes:
        name: 工具名称（对模型暴露的唯一标识）。
        description: 工具用途说明，指导模型何时调用。
        parameters: JSON Schema 格式的参数定义。
        read_only: 是否为只读工具（只读工具可配置免审批）。
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = False

    def to_function_schema(self) -> dict[str, Any]:
        """转换为 OpenAI function calling 格式的 schema。

        Returns:
            ``{"type": "function", "function": {...}}`` 结构。
        """
        function: dict[str, Any] = {"name": self.name, "description": self.description}
        if self.parameters:
            function["parameters"] = self.parameters
        return {"type": "function", "function": function}


class PermissionDecision(str, Enum):
    """工具调用的权限决策结果。

    Attributes:
        APPROVE: 批准本次调用。
        DENY: 拒绝本次调用。
        APPROVE_ALL: 批准本次调用，且本会话后续调用全部免审批。
        DENY_ALL: 拒绝本次调用，且本会话后续调用全部拒绝。
    """

    APPROVE = "approve"
    DENY = "deny"
    APPROVE_ALL = "approve_all"
    DENY_ALL = "deny_all"
