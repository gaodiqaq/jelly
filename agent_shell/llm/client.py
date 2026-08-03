"""LLM 客户端：基于 litellm 的模型调用封装。

负责:
- 将内部 Message 模型转换为 litellm/OpenAI 格式（含 tool_calls 的 JSON 序列化）
- 流式与非流式两种调用路径（默认流式）
- 将 litellm 异常映射为结构化 :class:`~agent_shell.errors.LLMError`
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Sequence
from typing import Any

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm
from litellm import exceptions as litellm_exc

from agent_shell.config import Settings
from agent_shell.errors import LLMError
from agent_shell.types import AssistantMessage, Message, ToolCall, ToolSpec

_DROP_PARAMS = True


class LLMClient:
    """封装 litellm 的对话补全客户端。

    Args:
        settings: 全局配置（模型名、采样参数、超时）。

    Raises:
        LLMError: 构造时无法导入 litellm（依赖缺失）。
    """

    def __init__(self, settings: Settings) -> None:
        self._model = settings.model
        self._temperature = settings.api.temperature
        self._max_tokens = settings.api.max_tokens
        self._timeout = settings.api.timeout
        litellm.drop_params = _DROP_PARAMS

    @property
    def model(self) -> str:
        """当前模型名。"""
        return self._model

    @model.setter
    def model(self, value: str) -> None:
        """切换模型名（运行时 /model 命令使用）。"""
        self._model = value

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None = None,
        *,
        stream: bool = True,
        on_token: Callable[[str], None] | None = None,
    ) -> AssistantMessage:
        """调用模型补全对话。

        Args:
            messages: 消息历史（内部模型）。
            tools: 可用的工具声明；None 表示纯对话。
            stream: 是否流式接收。
            on_token: 流式文本回调（每次收到文本增量时调用，用于实时渲染）。

        Returns:
            模型回复；含 tool_calls 时 content 可能为 None。

        Raises:
            LLMError: API 不可用、认证失败、限流、参数非法、模型不支持等，
                错误信息为面向用户的中文描述。
        """
        litellm_messages = self._to_litellm_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": litellm_messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "timeout": self._timeout,
        }
        if tools:
            kwargs["tools"] = [spec.to_function_schema() for spec in tools]
            kwargs["tool_choice"] = "auto"
        try:
            if stream:
                return self._complete_stream(on_token=on_token, **kwargs)
            return self._complete_nonstream(**kwargs)
        except LLMError:
            raise
        except KeyboardInterrupt as exc:
            raise LLMError("模型调用被用户中断", retryable=True) from exc

    def _complete_nonstream(self, **kwargs: Any) -> AssistantMessage:
        """非流式补全路径。

        Args:
            **kwargs: 传给 litellm.completion 的参数。

        Returns:
            模型回复。

        Raises:
            LLMError: 任何 API 异常（已映射）。
        """
        try:
            response = litellm.completion(**kwargs)
            message = response.choices[0].message
            tool_calls = self._parse_tool_calls(message)
            return AssistantMessage(content=message.content, tool_calls=tool_calls)
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一映射 litellm 异常
            raise self._map_error(exc) from exc

    def _complete_stream(
        self, *, on_token: Callable[[str], None] | None = None, **kwargs: Any
    ) -> AssistantMessage:
        """流式补全路径：累积文本与工具调用增量。

        流式片段中工具调用按 index 分片（name 在首个片段、arguments 逐段累积），
        结束时合并并解析 arguments JSON。

        Args:
            on_token: 文本增量回调（实时渲染）。
            **kwargs: 传给 litellm.completion 的参数。

        Returns:
            模型回复。

        Raises:
            LLMError: 任何 API 异常（已映射）。
        """
        content_parts: list[str] = []
        tool_chunks: dict[int, dict[str, Any]] = {}
        try:
            response = litellm.completion(stream=True, **kwargs)
            for chunk in response:
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                if getattr(delta, "content", None):
                    content_parts.append(delta.content)
                    if on_token is not None:
                        on_token(delta.content)
                for tc in getattr(delta, "tool_calls", None) or []:
                    index = tc.index
                    bucket = tool_chunks.setdefault(
                        index,
                        {"id": None, "name": "", "arguments": ""},
                    )
                    if getattr(tc, "id", None):
                        bucket["id"] = tc.id
                    if tc.function is not None:
                        if tc.function.name:
                            bucket["name"] += tc.function.name
                        if tc.function.arguments:
                            bucket["arguments"] += tc.function.arguments
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一映射 litellm 异常
            raise self._map_error(exc) from exc

        tool_calls: list[ToolCall] = []
        for index in sorted(tool_chunks):
            raw = tool_chunks[index]
            tool_calls.append(
                ToolCall(
                    id=raw["id"] or f"call_{index}",
                    name=raw["name"].strip(),
                    arguments=self._parse_arguments(raw["arguments"]),
                )
            )
        content = "".join(content_parts)
        return AssistantMessage(content=content or None, tool_calls=tool_calls)

    @staticmethod
    def _parse_tool_calls(message: Any) -> list[ToolCall]:
        """解析非流式响应中的工具调用。

        Args:
            message: litellm 响应中的 message 对象。

        Returns:
            工具调用列表（空列表表示无）。
        """
        calls: list[ToolCall] = []
        for raw in getattr(message, "tool_calls", None) or []:
            function = getattr(raw, "function", None)
            calls.append(
                ToolCall(
                    id=getattr(raw, "id", None) or f"call_{len(calls)}",
                    name=getattr(function, "name", "") if function else "",
                    arguments=LLMClient._parse_arguments(
                        getattr(function, "arguments", "") if function else ""
                    ),
                )
            )
        return calls

    @staticmethod
    def _parse_arguments(raw: str) -> dict[str, Any]:
        """解析工具调用参数的 JSON 字符串。

        Args:
            raw: 参数 JSON 字符串（可能为空白或非法）。

        Returns:
            参数字典；解析失败时返回含原始文本的占位字典。
        """
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"_raw_arguments": text}
        if not isinstance(parsed, dict):
            return {"_raw_arguments": text}
        return parsed

    @staticmethod
    def _to_litellm_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
        """将内部 Message 列表转换为 litellm 兼容字典。

        tool_calls 的 arguments 必须是 JSON 字符串（OpenAI 协议要求）。

        Args:
            messages: 内部消息列表。

        Returns:
            litellm 消息字典列表。
        """
        converted: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": message.content}
                if message.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": call.format_arguments(indent=0),
                            },
                        }
                        for call in message.tool_calls
                    ]
                converted.append(entry)
            elif message.role == "tool":
                converted.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id,
                        "name": message.name,
                        "content": message.content,
                    }
                )
            else:
                converted.append({"role": message.role, "content": message.content})
        return converted

    @staticmethod
    def _map_error(exc: Exception) -> LLMError:
        """将 litellm 异常映射为结构化 LLMError。

        Args:
            exc: 原始异常。

        Returns:
            带中文用户可读信息的 LLMError（保持异常链）。
        """
        auth_error_types: list[type[Exception]] = [litellm_exc.AuthenticationError]
        api_key_error = getattr(litellm_exc, "APIKeyError", None)
        if api_key_error is not None:
            auth_error_types.append(api_key_error)
        auth_detail = getattr(litellm_exc, "AuthenticationErrorDetail", None)
        if auth_detail is not None:
            auth_error_types.append(auth_detail)
        if isinstance(exc, tuple(auth_error_types)):
            return LLMError(
                f"API Key 无效或缺失（{type(exc).__name__}）: {exc}。"
                "请检查环境变量（如 OPENAI_API_KEY / ANTHROPIC_API_KEY）",
                retryable=False,
            )
        if isinstance(exc, litellm_exc.RateLimitError):
            return LLMError(f"触发限流: {exc}", retryable=True)
        timeout_types = [litellm_exc.Timeout, litellm_exc.APIConnectionError]
        timeout_cls = getattr(litellm_exc, "APITimeoutError", None)
        if timeout_cls is not None:
            timeout_types.append(timeout_cls)
        if isinstance(exc, tuple(timeout_types)):
            return LLMError(f"连接模型服务超时或失败: {exc}", retryable=True)
        if isinstance(exc, litellm_exc.ContentPolicyViolationError):
            return LLMError(f"请求被内容安全策略拒绝: {exc}", retryable=False)
        if isinstance(exc, litellm_exc.BadRequestError):
            return LLMError(f"请求参数非法: {exc}", retryable=False)
        if isinstance(exc, (litellm_exc.ServiceUnavailableError, litellm_exc.InternalServerError)):
            return LLMError(f"模型服务暂时不可用: {exc}", retryable=True)
        not_found_types = [litellm_exc.NotFoundError]
        model_not_supported = getattr(litellm_exc, "ModelNotSupportedError", None)
        if model_not_supported is not None:
            not_found_types.append(model_not_supported)
        if isinstance(exc, tuple(not_found_types)):
            return LLMError(
                f"模型不存在或提供商不支持: {exc}。请检查模型名（需含提供商前缀，"
                "如 openai/gpt-4o-mini）",
                retryable=False,
            )
        if isinstance(exc, litellm_exc.APIError):
            return LLMError(f"模型 API 错误: {exc}", retryable=True)
        return LLMError(
            f"未预期的模型调用错误（{type(exc).__name__}）: {exc}",
            retryable=True,
        )
