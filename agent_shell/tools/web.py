"""web_fetch 工具：抓取网页内容供模型分析。"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from agent_shell.tools.base import ToolContext, ToolRegistry
from agent_shell.types import ToolResult, ToolSpec

MAX_OUTPUT_MARK = "\n...[内容已截断]..."
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_DEFAULT_TIMEOUT = 30.0


class WebFetchArgs(BaseModel):
    """web_fetch 工具参数。"""

    url: str = Field(description="要抓取的完整 URL（含协议，如 https://example.com）")
    max_chars: int | None = Field(default=None, ge=100, le=200000, description="内容上限")


def _truncate(text: str, max_chars: int) -> str:
    """按字符数截断文本。

    Args:
        text: 原始文本。
        max_chars: 上限。

    Returns:
        截断后的文本（超限时附加标记）。
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + MAX_OUTPUT_MARK


def web_fetch(ctx: ToolContext, args: WebFetchArgs) -> ToolResult:
    """抓取 URL 的文本内容。

    仅返回纯文本（HTML 已剥离标签）；二进制内容（图片等）直接拒绝。

    Args:
        ctx: 工具上下文。
        args: URL 与内容上限。

    Returns:
        ToolResult: 网页文本；请求失败或内容非法时 ``is_error=True``。
    """
    limit = args.max_chars or ctx.max_output_chars
    try:
        response = httpx.get(
            args.url,
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
    except httpx.TimeoutException:
        return ToolResult(content=f"请求超时: {args.url}", is_error=True)
    except httpx.HTTPError as exc:
        return ToolResult(
            content=f"请求失败: {args.url}: {type(exc).__name__}: {exc}",
            is_error=True,
        )

    if response.status_code >= 400:
        return ToolResult(
            content=f"HTTP {response.status_code}: {args.url}",
            is_error=True,
        )
    content_type = response.headers.get("content-type", "").lower()
    if (
        content_type.startswith("image/")
        or content_type.startswith("audio/")
        or content_type.startswith("video/")
    ):
        return ToolResult(
            content=(
                f"目标为二进制内容（{content_type or '未知类型'}），无法作为文本处理: {args.url}"
            ),
            is_error=True,
        )
    try:
        text = response.text
    except UnicodeDecodeError as exc:
        return ToolResult(content=f"响应内容不是有效文本: {exc}", is_error=True)
    text = text.strip()
    if not text:
        return ToolResult(content=f"响应内容为空: {args.url}", is_error=True)
    return ToolResult(content=f"[{args.url}]\n{_truncate(text, limit)}")


def build_web_spec() -> tuple[ToolSpec, type[WebFetchArgs], Any]:
    """构建 web_fetch 工具声明。

    Returns:
        (spec, args_model, handler) 三元组。
    """
    spec = ToolSpec(
        name="web_fetch",
        description=(
            "抓取指定 URL 的网页文本内容。适用于查询文档、查看 API 文档、"
            "获取网页信息等场景。返回内容会被截断。"
        ),
        parameters=WebFetchArgs.model_json_schema(),
        read_only=True,
    )
    return spec, WebFetchArgs, web_fetch


def register_web(registry: ToolRegistry) -> None:
    """注册 web_fetch 工具。

    Args:
        registry: 目标注册表。
    """
    spec, args_model, handler = build_web_spec()
    registry.register(spec, args_model, handler)
