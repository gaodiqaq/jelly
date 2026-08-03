"""交互输入：用户输入读取与工具权限询问。

本模块只依赖 types 层；所有输出通过传入的 Console 完成。
"""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from agent_shell.types import PermissionDecision, ToolCall
from agent_shell.ui.console import console_supports_unicode

_PROMPT_SYMBOL = "❯" if console_supports_unicode() else ">"
_PROMPT_STR = f"{_PROMPT_SYMBOL} "
_HELP_TEXT = """\
[bold]可用命令[/bold]（以 / 开头）:
  /exit /quit      退出
  /help            显示本帮助
  /clear           清空会话历史（重新开始）
  /model <名称>    切换模型（如 /model openai/gpt-4o-mini）
  /auto            切换为自动审批模式（不再询问工具权限）
  /ask             切回逐个询问模式
  /session         显示当前会话 ID 与文件路径
  /tools           列出可用工具
  其余输入将作为指令发送给 Agent"""


def read_input(console: Console) -> str:
    """读取一行用户输入（跳过空行）。

    Args:
        console: 输出控制台。

    Returns:
        用户输入文本（不含换行）。
    """
    while True:
        try:
            text = console.input(_PROMPT_STR)
        except (EOFError, KeyboardInterrupt):
            raise
        stripped = text.strip()
        if stripped:
            return stripped


def print_help(console: Console) -> None:
    """打印帮助信息。

    Args:
        console: 输出控制台。
    """
    console.print(_HELP_TEXT)


def ask_permission(
    console: Console,
    call: ToolCall,
    tool_name: str,
    read_only: bool,
) -> PermissionDecision:
    """向用户询问是否允许一次工具调用。

    Args:
        console: 输出控制台。
        call: 工具调用详情。
        tool_name: 工具名称。
        read_only: 是否只读工具。

    Returns:
        用户的选择（approve / deny / approve_all / deny_all）。
    """
    badge = "只读" if read_only else "修改"
    body = Text()
    body.append(f"[{badge}] ", style="dim cyan" if read_only else "bold yellow")
    body.append(tool_name, style="bold")
    args_text = call.format_arguments()
    if args_text != "{}":
        body.append("\n" + args_text)
    console.print(
        Panel(
            body,
            title="[bold]权限请求[/bold]",
            border_style="magenta",
            expand=False,
        )
    )
    while True:
        choice = console.input(
            "允许本次调用？[bold]y[/bold]/[bold]n[/bold]（"
            "[bold]a[/bold] 本次会话始终允许 / [bold]d[/bold] 本次会话始终拒绝）: "
        )
        key = choice.strip().lower()
        mapping = {
            "y": PermissionDecision.APPROVE,
            "yes": PermissionDecision.APPROVE,
            "n": PermissionDecision.DENY,
            "no": PermissionDecision.DENY,
            "a": PermissionDecision.APPROVE_ALL,
            "always": PermissionDecision.APPROVE_ALL,
            "d": PermissionDecision.DENY_ALL,
            "deny": PermissionDecision.DENY_ALL,
        }
        if key in mapping:
            return mapping[key]
        console.print("[red]无效输入，请输入 y / n / a / d[/red]")


PermissionAsk = Callable[[ToolCall, str, bool], PermissionDecision]
