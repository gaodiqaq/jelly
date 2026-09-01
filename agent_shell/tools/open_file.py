"""open 工具：用系统默认程序打开文件 / 目录 / URL。

为什么需要它：
- bash 里跑 `notepad xxx` / `start xxx` 会**阻塞等待** GUI 程序退出，
  导致 bash 工具一直卡到超时（用户会话里 jelly 的"打开文件"就是这么失败的）
- 本工具用非阻塞方式启动默认关联程序，并立即返回

Windows: ``os.startfile``（资源管理器的"打开方式"语义）
macOS: ``open`` 命令
Linux: ``xdg-open`` 命令
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from agent_shell.tools.base import ToolContext, ToolRegistry
from agent_shell.types import ToolResult, ToolSpec


class OpenArgs(BaseModel):
    """open 工具参数。

    Attributes:
        path: 要打开的文件 / 目录 / URL（绝对或相对路径，或 http(s) 链接）。
    """

    path: str = Field(
        description="要打开的文件/目录/URL 路径（相对路径基于当前工作目录解析）"
    )


def sys_platform() -> str:
    """返回当前平台标记（os.name 无法区分 macOS/Linux）。"""
    import platform

    return platform.system()


def _resolve(ctx: ToolContext, path: str) -> str:
    """将路径解析为可打开的绝对路径（URL 保持原样）。"""
    p = path.strip()
    if p.startswith(("http://", "https://", "www.")):
        return p
    candidate = os.path.expanduser(p)
    if not os.path.isabs(candidate):
        candidate = os.path.join(str(ctx.cwd), candidate)
    return os.path.normpath(candidate)


def run_open(ctx: ToolContext, args: OpenArgs) -> ToolResult:
    """打开文件 / 目录 / URL（非阻塞）。

    Args:
        ctx: 工具执行上下文。
        args: 打开目标。

    Returns:
        ToolResult: 成功返回打开指令已发出的提示；目标不存在时 is_error=True。
    """
    target = _resolve(ctx, args.path)

    # URL 或不存在时给出明确错误
    is_url = target.startswith(("http://", "https://", "www."))
    if not is_url and not os.path.exists(target):
        return ToolResult(
            content=f"目标不存在: {target}（请检查路径；相对路径基于 {ctx.cwd} 解析）",
            is_error=True,
        )

    try:
        if os.name == "nt":
            # Windows: startfile 非阻塞，双击语义
            os.startfile(target)
        elif sys_platform() == "darwin":
            subprocess.Popen(["open", target])
        else:
            xdg = shutil.which("xdg-open") or "xdg-open"
            subprocess.Popen([xdg, target])
    except OSError as exc:
        return ToolResult(
            content=f"打开失败: {target}（{exc}）",
            is_error=True,
        )

    kind = "URL" if is_url else ("目录" if os.path.isdir(target) else "文件")
    return ToolResult(
        content=f"已用系统默认程序打开{kind}: {target}\n"
        "(注：该操作已交给系统，jelly 无法确认程序是否成功弹出；"
        "若没反应请检查默认关联程序。)"
    )


def build_open_spec() -> tuple[ToolSpec, type[OpenArgs], Any]:
    """构建 open 工具声明与实现。

    Returns:
        (spec, args_model, handler) 三元组。
    """
    spec = ToolSpec(
        name="open",
        description=(
            "用系统默认程序打开文件、目录或 URL（非阻塞，立即返回）。"
            "用于『打开这个文件』『打开文件夹』『打开网站』等需求。"
            "注意：不要用 bash 运行 GUI 程序（会阻塞超时），一律用本工具。"
        ),
        parameters=OpenArgs.model_json_schema(),
        read_only=False,
    )
    return spec, OpenArgs, run_open


def register_open(registry: ToolRegistry) -> None:
    """将 open 工具注册到注册表。

    Args:
        registry: 目标注册表。
    """
    spec, args_model, handler = build_open_spec()
    registry.register(spec, args_model, handler)