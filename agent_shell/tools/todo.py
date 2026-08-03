"""todo 工具：任务清单管理，帮助模型在长任务中跟踪进度。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent_shell.tools.base import ToolContext, ToolRegistry
from agent_shell.tools.todo_store import TodoStore
from agent_shell.types import ToolResult, ToolSpec


class TodoAddArgs(BaseModel):
    """todo_add 工具参数。"""

    content: str = Field(description="要添加的任务描述")


class TodoDoneArgs(BaseModel):
    """todo_done 工具参数。"""

    id: int = Field(ge=1, description="任务编号")
    done: bool = Field(default=True, description="true 标记完成，false 标记未完成")


class TodoListArgs(BaseModel):
    """todo_list 工具参数（无字段）。"""


def todo_add(ctx: ToolContext, args: TodoAddArgs) -> ToolResult:
    """新增一条待办事项。

    Args:
        ctx: 工具上下文（含 TodoStore）。
        args: 任务描述。

    Returns:
        ToolResult: 新增后的完整清单。
    """
    item = ctx.todo.add(args.content)
    return ToolResult(
        content=f"已添加任务 #{item.id}: {args.content}\n当前清单:\n{ctx.todo.render()}"
    )


def todo_done(ctx: ToolContext, args: TodoDoneArgs) -> ToolResult:
    """更新待办事项完成状态。

    Args:
        ctx: 工具上下文（含 TodoStore）。
        args: 事项编号与目标状态。

    Returns:
        ToolResult: 更新结果与完整清单；编号不存在时 ``is_error=True``。
    """
    if not ctx.todo.set_done(args.id, args.done):
        return ToolResult(content=f"任务 #{args.id} 不存在", is_error=True)
    state = "完成" if args.done else "未完成"
    return ToolResult(content=f"任务 #{args.id} 已标记为{state}\n当前清单:\n{ctx.todo.render()}")


def todo_list(ctx: ToolContext, args: TodoListArgs) -> ToolResult:
    """查看当前任务清单。

    Args:
        ctx: 工具上下文（含 TodoStore）。
        args: 无参数。

    Returns:
        ToolResult: 完整任务清单。
    """
    return ToolResult(content=ctx.todo.render())


def build_todo_specs() -> list[tuple[ToolSpec, type[BaseModel], Any]]:
    """构建 todo 工具的声明。

    Returns:
        (spec, args_model, handler) 三元组列表。
    """
    return [
        (
            ToolSpec(
                name="todo_add",
                description=(
                    "在任务清单中新增一条待办事项。处理多步骤任务时，"
                    "用清单跟踪计划与进度，并在步骤完成后更新。"
                ),
                parameters=TodoAddArgs.model_json_schema(),
                read_only=False,
            ),
            TodoAddArgs,
            todo_add,
        ),
        (
            ToolSpec(
                name="todo_done",
                description="将任务清单中的某条事项标记为完成/未完成（按编号）。",
                parameters=TodoDoneArgs.model_json_schema(),
                read_only=False,
            ),
            TodoDoneArgs,
            todo_done,
        ),
        (
            ToolSpec(
                name="todo_list",
                description="查看当前任务清单的全部事项及状态。",
                parameters=TodoListArgs.model_json_schema(),
                read_only=True,
            ),
            TodoListArgs,
            todo_list,
        ),
    ]


def register_todo(registry: ToolRegistry) -> None:
    """注册全部 todo 工具。

    Args:
        registry: 目标注册表。
    """
    for spec, args_model, handler in build_todo_specs():
        registry.register(spec, args_model, handler)


def build_todo_store() -> TodoStore:
    """构建新的任务清单存储。

    Returns:
        空的 TodoStore 实例。
    """
    return TodoStore()
