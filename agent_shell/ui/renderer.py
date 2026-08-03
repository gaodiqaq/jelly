"""会话事件渲染器：将 Agent 回调事件渲染为终端输出。

本模块只依赖 types 层与 rich，不依赖 core/tools/llm 的任何实现。
"""

from __future__ import annotations

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.status import Status
from rich.text import Text

from agent_shell.types import ToolCall, ToolResult
from agent_shell.ui.console import console_supports_unicode

_MAX_DISPLAY_CHARS = 50000
_TRUNCATION_MARK = "\n…（内容过长，终端显示已截断）"
_PROMPT_SYMBOL = "❯" if console_supports_unicode() else ">"


class Renderer:
    """把 Agent 生命周期事件渲染到终端。

    Args:
        console: 富文本控制台。
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._status: Status | None = None
        self._assistant_panel: Panel | None = None
        self._assistant_text: list[str] = []

    # ---------- 布局 ----------

    def header(self, model: str, cwd: str, version: str) -> None:
        """打印启动横幅。

        Args:
            model: 模型名。
            cwd: 工作目录。
            version: 版本号。
        """
        self._console.print(
            Rule(
                f"[bold cyan]果冻[/bold cyan] v{version} · {model}",
                style="dim",
            )
        )
        self._console.print(f"[dim]cwd: {cwd}[/dim]\n")

    def rule(self, title: str = "") -> None:
        """打印分隔线。

        Args:
            title: 分隔线标题（可空）。
        """
        self._console.print(Rule(title, style="dim"))

    # ---------- 用户输入 ----------

    def user_message(self, text: str) -> None:
        """渲染用户输入。

        Args:
            text: 用户文本。
        """
        self._console.print(f"[bold green]{_PROMPT_SYMBOL}[/bold green] {text}")

    # ---------- 状态与流式输出 ----------

    def begin_status(self, message: str) -> None:
        """开启旋转等待提示。

        Args:
            message: 提示文本。
        """
        self.stop_status()
        self._status = self._console.status(message, spinner="dots")

    def update_status(self, message: str) -> None:
        """更新旋转等待提示文本。

        Args:
            message: 新提示文本。
        """
        if self._status is not None:
            self._status.update(message)

    def stop_status(self) -> None:
        """关闭旋转等待提示。"""
        if self._status is not None:
            self._status.stop()
            self._status = None

    def begin_assistant(self) -> None:
        """开始一次助手回复（实时流式渲染）。"""
        self.stop_status()
        self._console.print("")

    def stream_token(self, token: str) -> None:
        """实时渲染流式文本片段。

        Args:
            token: 文本增量。
        """
        self._console.print(token, end="", soft_wrap=True, highlight=False)

    def end_assistant(self) -> None:
        """结束助手回复：输出换行收尾。"""
        self._console.print("")

    # ---------- 工具事件 ----------

    def tool_call(self, call: ToolCall) -> None:
        """渲染一次工具调用请求。

        Args:
            call: 工具调用。
        """
        self.stop_status()
        args_json = call.format_arguments()
        body = Group(
            Text(f"[{call.name}]", style="bold"),
            Text(args_json[:2000] + ("…" if len(args_json) > 2000 else "")),
        )
        self._console.print(
            Panel(body, title="[yellow]工具调用[/yellow]", border_style="yellow", expand=False)
        )

    def tool_result(self, result: ToolResult) -> None:
        """渲染一次工具执行结果。

        Args:
            result: 执行结果。
        """
        content = result.content
        if len(content) > _MAX_DISPLAY_CHARS:
            content = content[:_MAX_DISPLAY_CHARS] + _TRUNCATION_MARK
        style = "red" if result.is_error else "green"
        title = "工具执行失败" if result.is_error else "工具执行成功"
        self._console.print(
            Panel(
                Text(content, style=style, no_wrap=False, overflow="fold"),
                title=f"[{style}]{title}[/{style}]",
                border_style=style,
                expand=False,
            )
        )

    # ---------- 消息与错误 ----------

    def message(self, content: str | None) -> None:
        """渲染一条完整回复（非流式路径）。

        Args:
            content: 回复文本。
        """
        if not content:
            return
        self.stop_status()
        self._console.print("")
        self._console.print(Markdown(content))

    def error(self, message: str) -> None:
        """渲染错误信息。

        Args:
            message: 错误描述。
        """
        self.stop_status()
        self._console.print(
            Panel(
                Text(message, style="bold red"),
                title="[red]错误[/red]",
                border_style="red",
                expand=False,
            )
        )

    def info(self, message: str) -> None:
        """渲染普通提示信息。

        Args:
            message: 提示文本。
        """
        self.stop_status()
        self._console.print(f"[dim]{message}[/dim]")
