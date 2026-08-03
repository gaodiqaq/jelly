"""tools 层基础框架：执行上下文、工具注册表与参数校验。

工具函数的统一签名: ``fn(ctx: ToolContext, args: ArgsModel) -> ToolResult``。
本层禁止任何终端输出（print/rich），所有结果一律通过返回值
:class:`~agent_shell.types.ToolResult` 向上传递。
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from agent_shell.tools.todo_store import TodoStore
from agent_shell.types import ToolResult, ToolSpec

__all__ = [
    "ToolContext",
    "ToolRegistry",
    "ToolHandler",
    "ArgumentsError",
    "ArgsModel",
]


@dataclass(slots=True)
class ToolContext:
    """工具执行上下文，由 core 层在每次调用时注入。

    Attributes:
        cwd: 当前工作目录（相对路径基于它解析）。
        bash_timeout: bash 工具默认超时秒数。
        max_output_chars: 工具输出截断上限。
        todo: 任务清单存储（todo 系列工具共享状态）。
    """

    cwd: Path = field(default_factory=Path.cwd)
    bash_timeout: float = 120.0
    max_output_chars: int = 30000
    todo: TodoStore = field(default_factory=TodoStore)


class ArgumentsError(Exception):
    """工具参数非法（由注册表在 pydantic 校验失败时抛出）。"""


ToolHandler = Callable[[ToolContext, BaseModel], ToolResult]

ArgsModel = type[BaseModel]


class ToolRegistry:
    """工具注册表：维护名称 -> (ToolSpec, handler) 映射并负责分发。

    Args:
        cwd: 所有工具共享的默认工作目录。
        bash_timeout: bash 工具默认超时。
        max_output_chars: 工具输出截断上限。
        todo: 任务清单存储实例（共享跨工具状态）。
    """

    def __init__(
        self,
        *,
        cwd: Path,
        bash_timeout: float = 120.0,
        max_output_chars: int = 30000,
        todo: TodoStore | None = None,
    ) -> None:
        self._cwd = cwd
        self._bash_timeout = bash_timeout
        self._max_output_chars = max_output_chars
        self._todo = todo or TodoStore()
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}
        self._args_models: dict[str, ArgsModel] = {}

    def register(self, spec: ToolSpec, args_model: ArgsModel, handler: ToolHandler) -> None:
        """注册一个工具。

        Args:
            spec: 工具声明元数据。
            args_model: 参数校验模型（pydantic 类）。
            handler: 工具实现函数，签名 ``(ctx, args) -> ToolResult``。

        Raises:
            ValueError: 工具名重复注册。
        """
        if spec.name in self._specs:
            raise ValueError(f"工具重复注册: {spec.name}")
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler
        self._args_models[spec.name] = args_model

    def specs(self) -> list[ToolSpec]:
        """返回全部已注册工具的声明列表（按注册顺序）。"""
        return list(self._specs.values())

    def get_spec(self, name: str) -> ToolSpec | None:
        """按名称查询工具声明。

        Args:
            name: 工具名。

        Returns:
            工具声明；未注册返回 None。
        """
        return self._specs.get(name)

    def disable(self, name: str) -> None:
        """从注册表中移除一个工具。

        Args:
            name: 工具名。

        Raises:
            ValueError: 工具未注册。
        """
        if name not in self._specs:
            raise ValueError(f"工具未注册: {name}")
        del self._specs[name]
        del self._handlers[name]
        del self._args_models[name]

    def schemas(self) -> list[dict[str, Any]]:
        """生成 OpenAI function calling 格式的 schema 列表。"""
        return [spec.to_function_schema() for spec in self._specs.values()]

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        """执行一次工具调用（含参数校验）。

        Args:
            name: 工具名。
            arguments: 原始参数字典。

        Returns:
            结构化执行结果；未知工具、参数非法、执行异常时
            ``is_error=True`` 且 content 为中文错误说明。
        """
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult(
                content=f"未知工具: {name}。可用工具: {', '.join(self._specs) or '无'}",
                is_error=True,
            )
        handler = self._handlers[name]
        context = ToolContext(
            cwd=self._cwd,
            bash_timeout=self._bash_timeout,
            max_output_chars=self._max_output_chars,
            todo=self._todo,
        )
        try:
            parsed = self._args_models[name].model_validate(arguments)
        except ValidationError as exc:
            return ToolResult(
                content=f"参数校验失败: {exc.errors(include_url=False)}",
                is_error=True,
            )
        try:
            return handler(context, parsed)
        except ArgumentsError as exc:
            return ToolResult(content=f"参数错误: {exc}", is_error=True)
        except FileNotFoundError as exc:
            return ToolResult(content=f"文件不存在: {exc}", is_error=True)
        except PermissionError as exc:
            return ToolResult(content=f"权限不足: {exc}", is_error=True)
        except OSError as exc:
            return ToolResult(content=f"文件系统错误: {exc}", is_error=True)
        except Exception as exc:  # noqa: BLE001 - 工具内部未预期异常必须兜底
            return ToolResult(
                content=(
                    f"工具 {name} 执行异常: {type(exc).__name__}: {exc}\n"
                    f"{traceback.format_exc(limit=8)}"
                ),
                is_error=True,
            )
