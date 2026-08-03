"""工具执行器：权限决策 + 注册表分发。

权限模式（由配置与用户交互共同决定）:
- ``ask``: 修改性工具逐个询问用户；只读工具按配置免审批
- ``auto``: 全部免审批
- ``deny``: 全部拒绝

用户可在询问时选择"本次会话始终允许/拒绝"（APPROVE_ALL / DENY_ALL），
该决策由执行器记住并持续生效。
"""

from __future__ import annotations

from collections.abc import Callable

from agent_shell.tools.base import ToolRegistry
from agent_shell.types import PermissionDecision, ToolCall, ToolResult

PermissionAsk = Callable[[ToolCall, str, bool], PermissionDecision]

_DENY_MESSAGE = "工具调用被用户拒绝（该结果已回传模型，可尝试其他方案或征询用户）"


class ToolExecutor:
    """负责工具调用的权限检查与实际执行。

    Args:
        registry: 已注册的工具表。
        ask: 权限询问回调（由 ui 层提供）；None 表示不询问。
        default_permission: 初始权限模式，``ask`` / ``auto`` / ``deny``。
        auto_approve_read_only: 只读工具是否免审批。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        ask: PermissionAsk | None,
        *,
        default_permission: str = "ask",
        auto_approve_read_only: bool = True,
    ) -> None:
        self._registry = registry
        self._ask = ask
        self._auto = default_permission == "auto"
        self._deny_all = default_permission == "deny"
        self._auto_approve_read_only = auto_approve_read_only
        self._approved_all: set[str] = set()

    @property
    def registry(self) -> ToolRegistry:
        """底层工具注册表。"""
        return self._registry

    @property
    def auto_mode(self) -> bool:
        """当前是否为全自动审批模式。"""
        return self._auto

    def enable_auto(self) -> None:
        """切换为全自动审批模式（/permissions auto 命令）。"""
        self._auto = True

    def disable_auto(self) -> None:
        """退出全自动审批模式（/permissions ask 命令）。"""
        self._auto = False
        self._deny_all = False
        self._approved_all.clear()

    def execute(self, call: ToolCall) -> ToolResult:
        """执行一次工具调用（含权限检查与参数校验）。

        Args:
            call: 模型发起的工具调用。

        Returns:
            执行结果；被拒绝/工具不存在/参数非法时 ``is_error=True``。
        """
        spec = self._registry.get_spec(call.name)
        if spec is None:
            available = ", ".join(s.name for s in self._registry.specs())
            return ToolResult(
                content=f"工具不存在: {call.name}（可用: {available}）",
                is_error=True,
            )
        decision = self._decide(call, spec.read_only)
        if decision in (PermissionDecision.DENY, PermissionDecision.DENY_ALL):
            return ToolResult(content=_DENY_MESSAGE, is_error=True)
        return self._registry.call(call.name, call.arguments)

    def _decide(self, call: ToolCall, read_only: bool) -> PermissionDecision:
        """计算权限决策。

        Args:
            call: 工具调用（用于展示给用户）。
            read_only: 是否只读工具。

        Returns:
            权限决策结果。
        """
        if self._deny_all:
            return PermissionDecision.DENY
        if self._auto:
            return PermissionDecision.APPROVE
        if read_only and self._auto_approve_read_only:
            return PermissionDecision.APPROVE
        if call.name in self._approved_all:
            return PermissionDecision.APPROVE
        if self._ask is None:
            return PermissionDecision.APPROVE
        decision = self._ask(call, call.name, read_only)
        if decision == PermissionDecision.APPROVE_ALL:
            self._approved_all.add(call.name)
        elif decision == PermissionDecision.DENY_ALL:
            self._deny_all = True
        return decision
