"""core 层：执行器权限决策与分发测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_shell.core.executor import ToolExecutor
from agent_shell.tools import build_registry
from agent_shell.types import PermissionDecision, ToolCall


@pytest.fixture()
def executor(tmp_path: Path) -> ToolExecutor:
    """默认（ask 模式、无回调）执行器。"""
    registry = build_registry(cwd=tmp_path)
    return ToolExecutor(registry, None)


def test_auto_approve_when_no_ask_callback(tmp_path: Path) -> None:
    """无询问回调时直接放行（等价 auto）。"""
    registry = build_registry(cwd=tmp_path)
    executor = ToolExecutor(registry, None)
    result = executor.execute(ToolCall(id="c1", name="bash", arguments={"command": "echo hi"}))
    assert not result.is_error


def test_read_only_auto_approved_in_ask_mode(tmp_path: Path) -> None:
    """ask 模式下只读工具免审批。"""
    asked: list[str] = []
    registry = build_registry(cwd=tmp_path)

    def ask(call, name, read_only) -> PermissionDecision:
        asked.append(name)
        return PermissionDecision.APPROVE

    executor = ToolExecutor(registry, ask)
    executor.execute(ToolCall(id="c1", name="ls", arguments={"path": "."}))
    assert asked == []


def test_deny_returns_error_result(tmp_path: Path) -> None:
    """拒绝的工具调用返回 is_error 结果。"""
    registry = build_registry(cwd=tmp_path)

    def ask(call, name, read_only) -> PermissionDecision:
        return PermissionDecision.DENY

    executor = ToolExecutor(registry, ask)
    result = executor.execute(ToolCall(id="c1", name="bash", arguments={"command": "echo x"}))
    assert result.is_error
    assert "拒绝" in result.content


def test_approve_all_skips_future_asks(tmp_path: Path) -> None:
    """APPROVE_ALL 后同工具不再询问。"""
    registry = build_registry(cwd=tmp_path)
    asked: list[str] = []

    def ask(call, name, read_only) -> PermissionDecision:
        asked.append(name)
        return PermissionDecision.APPROVE_ALL

    executor = ToolExecutor(registry, ask)
    executor.execute(ToolCall(id="c1", name="bash", arguments={"command": "echo x"}))
    executor.execute(ToolCall(id="c2", name="bash", arguments={"command": "echo y"}))
    assert asked == ["bash"]


def test_deny_all_blocks_subsequent_calls(tmp_path: Path) -> None:
    """DENY_ALL 后所有工具调用直接拒绝。"""
    registry = build_registry(cwd=tmp_path)
    asks: list[PermissionDecision] = [PermissionDecision.DENY_ALL]

    def ask(call, name, read_only) -> PermissionDecision:
        return asks.pop(0)

    executor = ToolExecutor(registry, ask)
    first = executor.execute(ToolCall(id="c1", name="bash", arguments={"command": "echo x"}))
    second = executor.execute(ToolCall(id="c2", name="bash", arguments={"command": "echo y"}))
    assert first.is_error
    assert second.is_error


def test_unknown_tool_rejected_even_in_auto_mode(tmp_path: Path) -> None:
    """auto 模式下未知工具仍返回错误。"""
    registry = build_registry(cwd=tmp_path)
    executor = ToolExecutor(registry, None, default_permission="auto")
    result = executor.execute(ToolCall(id="c1", name="ghost", arguments={}))
    assert result.is_error
    assert "工具不存在" in result.content


def test_default_deny_mode(tmp_path: Path) -> None:
    """default_permission=deny 时所有调用被拒。"""
    registry = build_registry(cwd=tmp_path)
    executor = ToolExecutor(registry, None, default_permission="deny")
    result = executor.execute(ToolCall(id="c1", name="ls", arguments={"path": "."}))
    assert result.is_error


def test_enable_and_disable_auto(tmp_path: Path) -> None:
    """enable_auto / disable_auto 切换模式。"""
    registry = build_registry(cwd=tmp_path)
    asked: list[str] = []

    def ask(call, name, read_only) -> PermissionDecision:
        asked.append(name)
        return PermissionDecision.DENY

    executor = ToolExecutor(registry, ask)
    executor.enable_auto()
    result = executor.execute(ToolCall(id="c1", name="bash", arguments={"command": "echo x"}))
    assert not result.is_error
    assert asked == []

    executor.disable_auto()
    result = executor.execute(ToolCall(id="c2", name="bash", arguments={"command": "echo x"}))
    assert result.is_error
    assert asked == ["bash"]


def test_execute_validates_arguments(tmp_path: Path) -> None:
    """参数非法时返回校验错误而非执行。"""
    registry = build_registry(cwd=tmp_path)
    executor = ToolExecutor(registry, None, default_permission="auto")
    result = executor.execute(ToolCall(id="c1", name="bash", arguments={"command": 42}))
    assert result.is_error
    assert "参数校验失败" in result.content
