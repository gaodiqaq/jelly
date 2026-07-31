"""types 层：消息模型、工具声明与权限枚举测试。"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from agent_shell.types import (
    AssistantMessage,
    Message,
    PermissionDecision,
    SystemMessage,
    ToolCall,
    ToolMessage,
    ToolSpec,
    UserMessage,
)

_ADAPTER = TypeAdapter(Message)


def test_message_discriminated_union_roundtrip() -> None:
    """带 role 判别字段的联合类型 JSON 往返一致。"""
    messages: list[Message] = [
        SystemMessage(content="sys"),
        UserMessage(content="hi"),
        AssistantMessage(
            content=None,
            tool_calls=[ToolCall(id="c1", name="ls", arguments={"path": "."})],
        ),
        ToolMessage(tool_call_id="c1", name="ls", content="ok", is_error=False),
    ]
    for message in messages:
        restored = _ADAPTER.validate_json(message.model_dump_json())
        assert restored == message
        assert restored.role == message.role


def test_tool_call_format_arguments() -> None:
    """参数序列化为紧凑 JSON（indent=0）。"""
    call = ToolCall(id="c1", name="bash", arguments={"command": "ls", "timeout": 5.0})
    assert call.format_arguments(indent=0) == '{"command":"ls","timeout":5.0}'
    assert call.format_arguments(indent=2) == '{\n  "command": "ls",\n  "timeout": 5.0\n}'


def test_assistant_message_requires_tool_call_id_for_tool_message() -> None:
    """ToolMessage 缺少 tool_call_id 时校验失败。"""
    with pytest.raises(ValidationError):
        TypeAdapter(Message).validate_python({"role": "tool", "name": "ls", "content": "x"})


def test_tool_call_rejects_extra_fields() -> None:
    """ToolCall 拒绝未知字段。"""
    with pytest.raises(ValidationError):
        ToolCall(id="c1", name="ls", arguments={}, extra="nope")


def test_tool_spec_function_schema() -> None:
    """ToolSpec 生成 OpenAI function schema。"""
    spec = ToolSpec(
        name="bash",
        description="run a command",
        parameters={"type": "object", "properties": {}},
        read_only=False,
    )
    expected = {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "run a command",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    assert spec.to_function_schema() == expected


def test_tool_spec_function_schema_without_parameters() -> None:
    """无参数工具不输出 parameters 键。"""
    spec = ToolSpec(name="noop", description="no params")
    assert spec.to_function_schema()["function"] == {"name": "noop", "description": "no params"}


def test_permission_decision_values() -> None:
    """权限枚举取值稳定。"""
    assert PermissionDecision.APPROVE.value == "approve"
    assert PermissionDecision.DENY_ALL.value == "deny_all"
