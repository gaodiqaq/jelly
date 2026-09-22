"""LLM 客户端：基于 litellm 的模型调用封装。

负责:
- 将内部 Message 模型转换为 litellm/OpenAI 格式（含 tool_calls 的 JSON 序列化）
- 流式与非流式两种调用路径（默认流式）
- 将 litellm 异常映射为结构化 :class:`~agent_shell.errors.LLMError`
- 运行时凭据解析：注入 :class:`~agent_shell.runtime.ProviderStore` 后，
  每次请求动态解析 model / api_key / api_base（支持热切换）
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import threading
from collections.abc import Callable, Sequence
from copy import copy
from typing import Any
from urllib.parse import urlparse

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import httpx
import litellm
from litellm import exceptions as litellm_exc
from openai import OpenAI

from agent_shell.config import Settings
from agent_shell.errors import LLMError
from agent_shell.runtime import ProviderStore
from agent_shell.types import AssistantMessage, Message, ToolCall, ToolSpec

_DROP_PARAMS = True

# 绕过系统代理的共享 httpx 客户端（内网 api_base 专用，进程级复用）
_PROXY_BYPASS_HTTP_CLIENT: httpx.Client | None = None


def is_private_base(api_base: str) -> bool:
    """判断 api_base 是否指向内网/环回地址。

    IP 字面量直接判断；域名走 DNS 解析（解析失败按非内网处理，
    保持默认代理行为）。

    Args:
        api_base: 模型服务的 Base URL。

    Returns:
        全部解析结果均为内网/环回/链路本地地址时 True。
    """
    host = urlparse(api_base).hostname
    if not host:
        return False
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addrs = [ipaddress.ip_address(info[4][0]) for info in infos]
    return bool(addrs) and all(a.is_private or a.is_loopback or a.is_link_local for a in addrs)


def proxy_bypass_client(api_base: str, api_key: str | None) -> OpenAI:
    """返回绕过系统代理的 OpenAI 客户端（直连内网 api_base）。

    背景：Windows 上 ``urllib.getproxies()`` 会回退读取注册表里的系统代理
    （Clash/v2rayN 等），httpx ``trust_env=True`` 时发往内网模型的请求会被
    代理拦截（502/超时）。显式指向内网/环回地址的 api_base 应当直连；
    公网 API 不受影响，仍尊重系统代理。

    Args:
        api_base: 模型服务 Base URL（会作为客户端的 base_url）。
        api_key: API Key（本地服务通常忽略，占位即可）。

    Returns:
        OpenAI SDK 客户端（共享底层 httpx 连接池）。
    """
    global _PROXY_BYPASS_HTTP_CLIENT
    if _PROXY_BYPASS_HTTP_CLIENT is None:
        _PROXY_BYPASS_HTTP_CLIENT = httpx.Client(trust_env=False, timeout=None)
    return OpenAI(
        base_url=api_base,
        api_key=api_key or "empty",
        http_client=_PROXY_BYPASS_HTTP_CLIENT,
    )


class LLMClient:
    """封装 litellm 的对话补全客户端。

    Args:
        settings: 全局配置（采样参数、超时、默认模型）。
        store: 运行时配置存储；提供后每次调用实时解析模型与凭据，
            未提供时退回默认模型 + 环境变量（``<PREFIX>_API_KEY``）。

    Raises:
        LLMError: 构造时无法导入 litellm（依赖缺失）。
    """

    def __init__(self, settings: Settings, store: ProviderStore | None = None) -> None:
        self._fallback_model = settings.model
        self._resolved_snapshot = None
        self._store = store
        self._temperature = settings.api.temperature
        self._max_tokens = settings.api.max_tokens
        self._timeout = settings.api.timeout
        litellm.drop_params = _DROP_PARAMS

    @property
    def model(self) -> str:
        """当前模型名（运行时存储优先）。"""
        if self._store is not None:
            return self._store.model
        return self._fallback_model

    @model.setter
    def model(self, value: str) -> None:
        """切换模型名（运行时命令使用，持久化）。"""
        if self._store is not None:
            self._store.set_model(value)
        else:
            self._fallback_model = value

    def set_api_key(self, provider: str, api_key: str) -> None:
        """运行时更新提供商的 API Key。

        Args:
            provider: 提供商名（如 ``openai``）。
            api_key: 明文 API Key。
        """
        if self._store is not None:
            self._store.upsert_provider(provider, api_key=api_key)

    def set_api_base(self, provider: str, api_base: str) -> None:
        """运行时更新提供商的 Base URL。

        Args:
            provider: 提供商名（如 ``deepseek``）。
            api_base: Base URL。
        """
        if self._store is not None:
            self._store.upsert_provider(provider, api_base=api_base)

    def _resolve(self, model: str | None = None) -> tuple[str, str | None, str | None]:
        """解析本次请求使用的模型与凭据。

        Args:
            model: 指定的模型名；None 使用当前激活模型。

        Returns:
            ``(model, api_key, api_base)`` 三元组。
        """
        if self._resolved_snapshot is not None:
            return self._resolved_snapshot
        if self._store is not None:
            return self._store.resolve(model)
        name = model or self._fallback_model
        prefix = name.split("/", 1)[0].upper()
        return (
            name,
            os.environ.get(f"{prefix}_API_KEY"),
            os.environ.get(f"{prefix}_API_BASE"),
        )

    def snapshot(self, model: str | None = None) -> LLMClient:
        """Freeze the model and credentials for one run without writing configuration."""
        client = copy(self)
        client._resolved_snapshot = self._resolve(model)
        client._fallback_model = client._resolved_snapshot[0]
        client._store = None
        return client

    def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None = None,
        *,
        stream: bool = True,
        model: str | None = None,
        on_token: Callable[[str], None] | None = None,
        on_usage: Callable[[dict], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> AssistantMessage:
        """调用模型补全对话。

        Args:
            messages: 消息历史（内部模型）。
            tools: 可用的工具声明；None 表示纯对话。
            stream: 是否流式接收。
            model: 覆盖当前模型（运行时切换/连通性测试用）；None 用当前值。
            on_token: 流式文本增量回调（实时渲染）。
            on_usage: 用量统计回调（每轮结束时调用）。
            cancel_event: 取消事件；置位时立即中断并抛出 AgentInterrupted。

        Returns:
            模型回复；含 tool_calls 时 content 可能为 None。

        Raises:
            LLMError: API 不可用、认证失败、限流、参数非法、模型不支持等，
                错误信息为面向用户的中文描述。
            AgentInterrupted: 用户请求取消（cancel_event 置位）。
        """
        from agent_shell.errors import AgentInterrupted

        model_name, api_key, api_base = self._resolve(model)
        litellm_messages = self._to_litellm_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": litellm_messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "timeout": self._timeout,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base
        if api_base and is_private_base(api_base):
            # 内网/环回地址直连：系统代理（Clash 等）会拦截内网请求返回 502
            kwargs["client"] = proxy_bypass_client(api_base, api_key)
        # Qwen 系列（本地 vLLM 常见）默认输出 reasoning_content、content 为空，
        # 导致客户端收不到回复；通过 chat_template_kwargs 关闭思考模式。
        if "qwen" in model_name.lower():
            kwargs.setdefault("chat_template_kwargs", {})["enable_thinking"] = False
        if tools:
            kwargs["tools"] = [spec.to_function_schema() for spec in tools]
            kwargs["tool_choice"] = "auto"
        try:
            if stream:
                return self._complete_stream(
                    on_token=on_token,
                    on_usage=on_usage,
                    cancel_event=cancel_event,
                    **kwargs,
                )
            return self._complete_nonstream(on_usage=on_usage, **kwargs)
        except LLMError:
            raise
        except AgentInterrupted:
            raise
        except KeyboardInterrupt as exc:
            raise LLMError("模型调用被用户中断", retryable=True) from exc

    def _complete_nonstream(
        self,
        on_usage: Callable[[dict], None] | None = None,
        **kwargs: Any,
    ) -> AssistantMessage:
        """非流式补全路径。

        Args:
            on_usage: 用量统计回调。
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
            if on_usage is not None:
                usage = getattr(response, "usage", None)
                if usage:
                    on_usage(
                        {
                            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                            "cache_creation_tokens": getattr(
                                usage, "cache_creation_input_tokens", 0
                            )
                            or 0,
                            "cache_read_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
                            "model": kwargs.get("model", ""),
                        }
                    )
            content = message.content
            if not content:
                # 推理模型可能只输出 reasoning_content（如未成功关闭思考的 Qwen）
                content = getattr(message, "reasoning_content", None)
            return AssistantMessage(content=content, tool_calls=tool_calls)
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一映射 litellm 异常
            raise self._map_error(exc) from exc

    def _complete_stream(
        self,
        *,
        on_token: Callable[[str], None] | None = None,
        on_usage: Callable[[dict], None] | None = None,
        cancel_event: threading.Event | None = None,
        **kwargs: Any,
    ) -> AssistantMessage:
        """流式补全路径：累积文本与工具调用增量。

        流式片段中工具调用按 index 分片（name 在首个片段、arguments 逐段累积），
        结束时合并并解析 arguments JSON。

        Args:
            on_token: 文本增量回调（实时渲染）。
            on_usage: 用量统计回调（每轮结束时调用）。
            cancel_event: 取消事件；置位时立即抛出 AgentInterrupted。
            **kwargs: 传给 litellm.completion 的参数。

        Returns:
            模型回复。

        Raises:
            LLMError: 任何 API 异常（已映射）。
            AgentInterrupted: 用户请求取消。
        """
        content_parts: list[str] = []
        tool_chunks: dict[int, dict[str, Any]] = {}
        model_name = kwargs.get("model", "")
        try:
            kwargs.setdefault("stream_options", {})["include_usage"] = True
            response = litellm.completion(stream=True, **kwargs)
            for chunk in response:
                # 检查取消请求，立即中断流式输出
                if cancel_event is not None and cancel_event.is_set():
                    from agent_shell.errors import AgentInterrupted

                    raise AgentInterrupted("已停止")
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is None:
                    hidden = getattr(chunk, "hidden_params", None)
                    if hidden:
                        chunk_usage = getattr(hidden, "token_usage", None)
                if chunk_usage is not None and on_usage is not None:
                    usage_data = {
                        "prompt_tokens": getattr(chunk_usage, "prompt_tokens", 0) or 0,
                        "completion_tokens": getattr(chunk_usage, "completion_tokens", 0) or 0,
                        "total_tokens": getattr(chunk_usage, "total_tokens", 0) or 0,
                        "cache_creation_tokens": getattr(
                            chunk_usage, "cache_creation_input_tokens", 0
                        )
                        or 0,
                        "cache_read_tokens": getattr(chunk_usage, "cache_read_input_tokens", 0)
                        or 0,
                        "model": model_name,
                    }
                    on_usage(usage_data)
                    continue
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                if getattr(delta, "content", None):
                    content_parts.append(delta.content)
                    if on_token is not None:
                        on_token(delta.content)
                elif getattr(delta, "reasoning_content", None):
                    # 推理模型：content 为空时用 reasoning_content 兜底，避免空回复
                    content_parts.append(delta.reasoning_content)
                    if on_token is not None:
                        on_token(delta.reasoning_content)
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
        except Exception as exc:
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
        # 注意：litellm 的 BadGatewayError 继承 openai 的 APIError，而非
        # litellm.exceptions.APIError，必须显式列出（否则落到兜底分支）
        server_error_types: list[type[Exception]] = [
            litellm_exc.ServiceUnavailableError,
            litellm_exc.InternalServerError,
        ]
        bad_gateway = getattr(litellm_exc, "BadGatewayError", None)
        if bad_gateway is not None:
            server_error_types.append(bad_gateway)
        if isinstance(exc, tuple(server_error_types)):
            return LLMError(
                f"模型服务暂时不可用（网关/服务端错误，可稍后重试）: {exc}",
                retryable=True,
            )
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
