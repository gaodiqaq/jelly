"""llm 层：消息转换与异常映射测试（纯离线，不发起网络请求）。"""

from __future__ import annotations

import pytest
from litellm import exceptions as litellm_exc

from agent_shell.errors import LLMError
from agent_shell.llm.client import LLMClient
from agent_shell.types import (
    AssistantMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)


def test_to_litellm_messages_plain() -> None:
    """普通消息转换为 litellm 字典。"""
    messages = [SystemMessage(content="sys"), UserMessage(content="hi")]
    converted = LLMClient._to_litellm_messages(messages)
    assert converted == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]


def test_to_litellm_messages_with_tool_calls() -> None:
    """assistant 工具调用转换为 OpenAI 格式（arguments 为 JSON 字符串）。"""
    messages = [
        AssistantMessage(
            content=None,
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="bash",
                    arguments={"command": "echo hi", "timeout": 5},
                )
            ],
        ),
        ToolMessage(tool_call_id="call_1", name="bash", content="hi", is_error=False),
    ]
    converted = LLMClient._to_litellm_messages(messages)
    assistant = converted[0]
    assert assistant["role"] == "assistant"
    assert assistant["content"] is None
    assert assistant["tool_calls"][0]["function"]["name"] == "bash"
    assert assistant["tool_calls"][0]["function"]["arguments"] == (
        '{"command":"echo hi","timeout":5}'
    )
    assert converted[1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "name": "bash",
        "content": "hi",
    }


def test_to_litellm_messages_unicode_arguments() -> None:
    """中文参数不被转义。"""
    messages = [
        AssistantMessage(
            content="ok",
            tool_calls=[ToolCall(id="c1", name="todo_add", arguments={"content": "写报告"})],
        )
    ]
    converted = LLMClient._to_litellm_messages(messages)
    assert "写报告" in converted[0]["tool_calls"][0]["function"]["arguments"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ("", {}),
        ("   ", {}),
        ("not-json", {"_raw_arguments": "not-json"}),
        ("[1,2]", {"_raw_arguments": "[1,2]"}),
    ],
)
def test_parse_arguments(raw: str, expected: dict) -> None:
    """参数 JSON 解析边界情况。"""
    assert LLMClient._parse_arguments(raw) == expected


def test_map_error_authentication() -> None:
    """认证错误映射为明确的 API Key 提示。"""
    error = litellm_exc.AuthenticationError(
        "bad key", llm_provider="openai", model="openai/gpt-4o-mini"
    )
    mapped = LLMClient._map_error(error)
    assert isinstance(mapped, LLMError)
    assert "API Key" in str(mapped)
    assert mapped.retryable is False


def test_map_error_rate_limit_is_retryable() -> None:
    """限流错误标记为可重试。"""
    error = litellm_exc.RateLimitError(
        "too many", llm_provider="openai", model="openai/gpt-4o-mini"
    )
    mapped = LLMClient._map_error(error)
    assert "限流" in str(mapped)
    assert mapped.retryable is True


def test_map_error_timeout_is_retryable() -> None:
    """超时错误标记为可重试。"""
    error = litellm_exc.Timeout("slow", llm_provider="openai", model="openai/gpt-4o-mini")
    mapped = LLMClient._map_error(error)
    assert mapped.retryable is True


def test_map_error_model_not_found() -> None:
    """模型不存在映射为模型名提示。"""
    error = litellm_exc.NotFoundError(
        "no such model", model="openai/gpt-4o-mini", llm_provider="openai"
    )
    mapped = LLMClient._map_error(error)
    assert "模型不存在" in str(mapped)
    assert mapped.retryable is False


def test_map_error_unknown_exception() -> None:
    """未知异常兜底为通用 LLMError。"""
    mapped = LLMClient._map_error(RuntimeError("boom"))
    assert isinstance(mapped, LLMError)
    assert "RuntimeError" in str(mapped)
