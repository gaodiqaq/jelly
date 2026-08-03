"""tools 层：本地工具执行。

导出 :func:`build_registry` 工厂函数，由 core 层调用以组装
完整的工具注册表（含共享 TodoStore 状态）。
"""

from __future__ import annotations

from pathlib import Path

from agent_shell.tools.base import (
    ArgumentsError,
    ToolContext,
    ToolRegistry,
)
from agent_shell.tools.bash import register_bash
from agent_shell.tools.fs import register_fs
from agent_shell.tools.search import register_search
from agent_shell.tools.todo import TodoStore, register_todo
from agent_shell.tools.web import register_web

__all__ = [
    "build_registry",
    "ToolRegistry",
    "ToolContext",
    "ArgumentsError",
    "TodoStore",
]


def build_registry(
    *,
    cwd: Path,
    bash_timeout: float = 120.0,
    max_output_chars: int = 30000,
    disabled: set[str] | None = None,
    todo: TodoStore | None = None,
) -> ToolRegistry:
    """组装内置工具注册表。

    Args:
        cwd: 工具默认工作目录。
        bash_timeout: bash 工具默认超时。
        max_output_chars: 工具输出截断上限。
        disabled: 需要禁用的工具名集合（空表示全部启用）。
        todo: 共享任务清单存储。

    Returns:
        已注册全部内置工具的 ToolRegistry。
    """
    registry = ToolRegistry(
        cwd=cwd,
        bash_timeout=bash_timeout,
        max_output_chars=max_output_chars,
        todo=todo,
    )
    for register in (register_bash, register_fs, register_search, register_todo, register_web):
        register(registry)
    if disabled:
        for name in sorted(disabled):
            registry.disable(name)
    return registry
