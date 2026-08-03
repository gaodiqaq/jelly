"""bash 工具：在本地 shell 中执行命令并返回输出。"""

from __future__ import annotations

import os
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from agent_shell.tools.base import ToolContext, ToolRegistry
from agent_shell.types import ToolResult, ToolSpec

MAX_OUTPUT_MARK = "\n...[输出已截断]..."


class BashArgs(BaseModel):
    """bash 工具参数。

    Attributes:
        command: 要执行的 shell 命令。
        timeout: 超时秒数（None 使用上下文默认值）。
    """

    command: str = Field(description="要执行的 shell 命令")
    timeout: float | None = Field(
        default=None, ge=1, le=3600, description="超时秒数，默认使用全局配置"
    )


def _shell_command(command: str) -> list[str]:
    """构造跨平台 shell 调用命令。

    Windows 使用 ``cmd /c``，其他平台使用 ``/bin/bash -lc``。

    Args:
        command: 用户命令。

    Returns:
        argv 列表。
    """
    if os.name == "nt":
        return ["cmd.exe", "/c", command]
    return ["/bin/bash", "-lc", command]


def _truncate(text: str, max_chars: int) -> str:
    """按字符数截断文本，超限时附加截断标记。

    Args:
        text: 原始输出。
        max_chars: 上限。

    Returns:
        截断后的文本。
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + MAX_OUTPUT_MARK


def run_bash(ctx: ToolContext, args: BashArgs) -> ToolResult:
    """执行 bash 命令并捕获输出。

    Args:
        ctx: 工具执行上下文（提供 cwd 与输出上限）。
        args: 命令与超时参数。

    Returns:
        ToolResult: 退出码非 0 时 ``is_error=True``，content 包含 stdout 与 stderr。
    """
    timeout = args.timeout if args.timeout is not None else ctx.bash_timeout
    try:
        completed = subprocess.run(
            _shell_command(args.command),
            cwd=str(ctx.cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        return ToolResult(
            content=(
                f"命令超时（>{timeout}s）: {args.command}\n"
                f"已输出:\n{_truncate(partial, ctx.max_output_chars)}"
            ),
            is_error=True,
        )
    except OSError as exc:
        return ToolResult(
            content=f"无法启动 shell: {exc}",
            is_error=True,
        )

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    stderr_block = f"\n[stderr]\n{stderr}" if stderr else ""
    content = f"$ {args.command}\n{stdout}{stderr_block}".rstrip()
    if completed.returncode != 0:
        content += f"\n[退出码: {completed.returncode}]"
    return ToolResult(
        content=_truncate(content, ctx.max_output_chars),
        is_error=completed.returncode != 0,
    )


def build_bash_spec() -> tuple[ToolSpec, type[BashArgs], Any]:
    """构建 bash 工具声明与实现。

    Returns:
        (spec, args_model, handler) 三元组。
    """
    spec = ToolSpec(
        name="bash",
        description=(
            "在本地终端执行 shell 命令。适用于文件操作、运行程序、git 操作、"
            "系统查询等任何无法用专用工具完成的任务。命令在当前工作目录执行，"
            "输出会被截断。修改性命令需要用户审批。"
        ),
        parameters=BashArgs.model_json_schema(),
        read_only=False,
    )
    return spec, BashArgs, run_bash


def register_bash(registry: ToolRegistry) -> None:
    """将 bash 工具注册到注册表。

    Args:
        registry: 目标注册表。
    """
    spec, args_model, handler = build_bash_spec()
    registry.register(spec, args_model, handler)
