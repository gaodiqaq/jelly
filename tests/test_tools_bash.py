"""tools 层：bash 与 todo 工具测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent_shell.tools.base import ToolContext
from agent_shell.tools.bash import BashArgs, run_bash
from agent_shell.tools.todo import (
    TodoAddArgs,
    TodoDoneArgs,
    TodoListArgs,
    todo_add,
    todo_done,
    todo_list,
)
from agent_shell.tools.todo_store import TodoStore


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    """临时 cwd 的 ToolContext。"""
    return ToolContext(cwd=tmp_path, bash_timeout=10.0)


def test_bash_echo(ctx: ToolContext) -> None:
    """bash 执行成功命令并返回输出。"""
    result = run_bash(ctx, BashArgs(command="echo hello"))
    assert not result.is_error
    assert "hello" in result.content


def test_bash_nonzero_exit_is_error(ctx: ToolContext) -> None:
    """非零退出码标记为错误并携带退出码。"""
    result = run_bash(ctx, BashArgs(command="exit 3"))
    assert result.is_error
    assert "退出码: 3" in result.content


def test_bash_cwd_respected(ctx: ToolContext, tmp_path: Path) -> None:
    """命令在当前工作目录执行。"""
    (tmp_path / "marker.txt").write_text("x", encoding="utf-8")
    if sys.platform == "win32":
        command = "dir /b"
    else:
        command = "python -c \"import os; print(os.listdir('.'))\""
    result = run_bash(ctx, BashArgs(command=command))
    assert not result.is_error
    assert "marker.txt" in result.content


def test_bash_timeout(ctx: ToolContext) -> None:
    """超时命令返回结构化错误。"""
    command = (
        'python -c "import time; time.sleep(30)"'
        if sys.platform != "win32"
        else "ping -n 30 127.0.0.1"
    )
    result = run_bash(ctx, BashArgs(command=command, timeout=1))
    assert result.is_error
    assert "超时" in result.content


def test_todo_lifecycle() -> None:
    """todo 添加/完成/查询全流程。"""
    store = TodoStore()
    ctx = ToolContext(cwd=Path.cwd(), todo=store)
    added = todo_add(ctx, TodoAddArgs(content="写测试"))
    assert not added.is_error
    assert "已添加任务 #1" in added.content
    listed = todo_list(ctx, TodoListArgs())
    assert "#1" in listed.content
    done = todo_done(ctx, TodoDoneArgs(id=1, done=True))
    assert not done.is_error
    assert "标记为完成" in done.content
    assert "[x] #1" in store.render()
    missing = todo_done(ctx, TodoDoneArgs(id=99, done=True))
    assert missing.is_error
    assert "不存在" in missing.content
