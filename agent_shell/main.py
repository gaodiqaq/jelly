"""agent-shell CLI 入口：组件装配、REPL 主循环与单次任务模式。

组装职责（依赖注入）:
- config -> Settings
- llm  -> LLMClient
- tools -> ToolRegistry（含 TodoStore）
- core -> Session / ToolExecutor / Agent
- ui  -> Renderer 与权限询问回调，通过 AgentCallbacks 注入 Agent
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console

from agent_shell import __version__
from agent_shell.config import Settings, load_settings
from agent_shell.core import Agent, AgentCallbacks, AgentInterrupted, LLMError, Session
from agent_shell.core.executor import ToolExecutor
from agent_shell.errors import ConfigError, SessionError
from agent_shell.llm.client import LLMClient
from agent_shell.llm.prompts import build_system_prompt
from agent_shell.tools import TodoStore, build_registry
from agent_shell.ui.console import _error_tolerant, create_console
from agent_shell.ui.prompt import ask_permission, print_help, read_input
from agent_shell.ui.renderer import Renderer

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def _load_dotenv_files() -> None:
    """从当前目录与用户目录加载 .env（缺失时静默忽略）。"""
    for path in (Path.cwd() / ".env", Path.home() / ".agent_shell" / ".env"):
        if path.is_file():
            load_dotenv(path, override=False)


def _build_agent(
    settings: Settings,
    console: Console,
    renderer: Renderer,
    *,
    single_shot: bool,
    stream: bool,
    session: Session | None = None,
) -> Agent:
    """装配 Agent 及其全部依赖组件。

    Args:
        settings: 全局配置。
        console: 输出控制台。
        renderer: 渲染器。
        single_shot: 单次任务模式。
        stream: 流式输出。
        session: 待恢复的会话；None 时创建新会话。

    Returns:
        装配完成的 Agent。
    """
    todo = TodoStore()
    registry = build_registry(
        cwd=settings.cwd,
        bash_timeout=settings.tools.bash_timeout,
        max_output_chars=settings.tools.max_output_chars,
        disabled=settings.tools.disabled,
        todo=todo,
    )
    llm = LLMClient(settings)
    if session is None:
        system_prompt = settings.system_prompt or build_system_prompt(settings.cwd, settings.model)
        session = Session.create(
            settings.session_dir,
            settings.model,
            settings.cwd,
            system_prompt=system_prompt,
        )
    executor = ToolExecutor(
        registry,
        lambda call, name, read_only: ask_permission(console, call, name, read_only),
        default_permission=settings.permissions.default,
        auto_approve_read_only=settings.permissions.auto_approve_read_only,
    )
    callbacks = _build_callbacks(renderer, stream)
    return Agent(
        settings,
        session,
        llm,
        executor,
        callbacks,
        single_shot=single_shot,
        stream=stream,
    )


def _build_callbacks(renderer: Renderer, stream: bool) -> AgentCallbacks:
    """构建 ui 事件回调。

    流式模式下第一个 token 到达时关闭旋转提示，之后实时渲染。

    Args:
        renderer: 渲染器。
        stream: 是否流式输出。

    Returns:
        AgentCallbacks 实例。
    """
    stream_started = False

    def on_status(text: str) -> None:
        renderer.begin_status(text)

    def on_token(token: str) -> None:
        nonlocal stream_started
        if not stream_started:
            stream_started = True
            renderer.stop_status()
        renderer.stream_token(token)

    def on_stream_end() -> None:
        nonlocal stream_started
        renderer.end_assistant()
        stream_started = False

    def on_tool_call(call) -> None:
        renderer.stop_status()
        renderer.tool_call(call)

    def on_tool_result(result) -> None:
        renderer.stop_status()
        renderer.tool_result(result)

    def on_message(content: str | None) -> None:
        renderer.stop_status()
        renderer.message(content)

    def on_llm_error(exc: LLMError) -> None:
        renderer.stop_status()
        renderer.error(f"{exc}")

    return AgentCallbacks(
        on_status=on_status,
        on_stream_start=renderer.begin_assistant if stream else None,
        on_token=on_token if stream else None,
        on_stream_end=on_stream_end if stream else None,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
        on_message=on_message if not stream else None,
        on_llm_error=on_llm_error,
    )


@app.command()
def run(
    prompt: Annotated[str | None, typer.Argument(help="直接执行此指令后退出（单次模式）")] = None,
    model: Annotated[
        str | None,
        typer.Option(help="模型名（litellm 格式，如 openai/gpt-4o-mini）"),
    ] = None,
    config: Annotated[Path | None, typer.Option(help="配置文件路径")] = None,
    cwd: Annotated[Path | None, typer.Option(help="工作目录（工具执行基目录）")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="自动审批所有工具调用")] = False,
    deny: Annotated[bool, typer.Option("--deny", "-D", help="拒绝所有工具调用")] = False,
    no_stream: Annotated[bool, typer.Option("--no-stream", help="关闭流式输出")] = False,
    max_turns: Annotated[int | None, typer.Option(help="单轮最大工具调用轮数")] = None,
    session_id: Annotated[str | None, typer.Option("--session", help="恢复指定会话")] = None,
) -> None:
    """启动 agent-shell：交互式 REPL（默认）或单次任务模式。"""
    _load_dotenv_files()
    permission = "auto" if yes else "deny" if deny else None
    try:
        settings = load_settings(
            config,
            model=model,
            max_turns=max_turns,
            permission=permission,
            cwd=cwd,
        )
    except ConfigError as exc:
        _print_fatal(f"配置错误: {exc}")
        raise typer.Exit(code=1) from exc

    console = create_console()
    renderer = Renderer(console)

    session: Session | None = None
    if session_id is not None:
        try:
            session = Session.resume(settings.session_dir, session_id)
        except SessionError as exc:
            _print_fatal(f"会话恢复失败: {exc}")
            raise typer.Exit(code=1) from exc
        settings.cwd = session.cwd

    agent = _build_agent(
        settings,
        console,
        renderer,
        single_shot=prompt is not None,
        stream=not no_stream,
        session=session,
    )
    renderer.header(settings.model, str(settings.cwd), __version__)
    if prompt is not None:
        _run_once(agent, renderer, console, prompt)
        return
    _repl(agent, renderer, console, settings)


def _run_once(agent: Agent, renderer: Renderer, console: Console, prompt: str) -> None:
    """单次任务模式：执行一条指令后退出。

    Args:
        agent: Agent 实例。
        renderer: 渲染器。
        console: 控制台。
        prompt: 指令文本。
    """
    renderer.user_message(prompt)
    try:
        agent.run(prompt)
    except AgentInterrupted:
        renderer.info("已中断")
    except LLMError:
        renderer.info("模型调用失败，本回合已结束（会话已保存）")
    renderer.info(f"会话已保存: {agent.session.file_path}")


def _repl(
    agent: Agent,
    renderer: Renderer,
    console: Console,
    settings: Settings,
) -> None:
    """交互式 REPL 主循环。

    Args:
        agent: Agent 实例。
        renderer: 渲染器。
        console: 控制台。
        settings: 全局配置。
    """
    renderer.info("输入 /help 查看命令，Ctrl+C 中断当前任务，/exit 退出")
    while True:
        try:
            text = read_input(console)
        except (EOFError, KeyboardInterrupt):
            console.print("")
            renderer.info("再见！")
            raise typer.Exit(code=0) from None
        if text.startswith("/"):
            _handle_command(text, agent, renderer, console, settings)
            continue
        try:
            agent.run(text)
        except AgentInterrupted:
            renderer.info("任务已中断")
        except LLMError:
            renderer.info("模型调用失败，本回合已结束（会话已保存）")


def _handle_command(
    text: str,
    agent: Agent,
    renderer: Renderer,
    console: Console,
    settings: Settings,
) -> None:
    """处理以 / 开头的内部命令。

    Args:
        agent: Agent 实例。
        renderer: 渲染器。
        console: 控制台。
        settings: 全局配置。

    Raises:
        typer.Exit: 用户请求退出（/exit、/quit）。
    """
    parts = text.split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1] if len(parts) > 1 else ""

    if command in ("/exit", "/quit"):
        raise typer.Exit(code=0)
    if command == "/help":
        print_help(console)
    elif command == "/clear":
        system_content = (
            agent.session.messages[0].content
            if agent.session.messages
            else build_system_prompt(agent.session.cwd, agent.session.model)
        )
        new_session = Session.create(
            agent.session.session_dir,
            agent.session.model,
            agent.session.cwd,
            system_prompt=system_content,
        )
        agent.replace_session(new_session)
        renderer.info("会话已清空")
    elif command == "/model":
        if not argument:
            renderer.info(f"当前模型: {settings.model}")
        else:
            agent.llm.model = argument
            settings.model = argument
            renderer.info(f"模型已切换为 {argument}")
    elif command == "/auto":
        agent.executor.enable_auto()
        renderer.info("已切换为自动审批模式")
    elif command == "/ask":
        agent.executor.disable_auto()
        renderer.info("已切回逐个询问模式")
    elif command == "/session":
        renderer.info(f"会话 ID: {agent.session.session_id}")
        renderer.info(f"文件: {agent.session.file_path}")
    elif command == "/tools":
        names = ", ".join(spec.name for spec in agent.executor.registry.specs())
        renderer.info(f"可用工具: {names}")
    else:
        console.print(f"[red]未知命令: {command}[/red]（/help 查看帮助）")


def _print_fatal(message: str) -> None:
    """打印致命错误（配置/会话层启动失败，唯一允许直接退出的场景）。

    Args:
        message: 错误信息。
    """
    console = Console(stderr=_error_tolerant(sys.stderr))
    console.print(f"[bold red]错误:[/bold red] {message}")


@app.command("sessions")
def list_sessions() -> None:
    """列出历史会话。"""
    _load_dotenv_files()
    try:
        settings = load_settings()
    except ConfigError as exc:
        _print_fatal(f"配置错误: {exc}")
        raise typer.Exit(code=1) from exc
    console = create_console()
    metas = Session.list_sessions(settings.session_dir)
    if not metas:
        console.print(f"[dim]暂无会话（目录: {settings.session_dir}）[/dim]")
        return
    console.print(f"[bold]共 {len(metas)} 个会话（目录: {settings.session_dir}）[/bold]")
    for meta in metas:
        console.print(
            f"  {meta.session_id}  {meta.updated_at[:19]}  "
            f"{meta.message_count} 条消息  {meta.model}  [dim]{meta.cwd}[/dim]"
        )
        console.print(f"    恢复: agent --session {meta.session_id}")


@app.command("web")
def serve_web(
    host: Annotated[str, typer.Option("--host", "-h", help="监听地址")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="监听端口")] = 8000,
) -> None:
    """启动 Web 服务（多用户浏览器界面，需 AGENT_WEB_TOKEN）。"""
    _load_dotenv_files()
    try:
        settings = load_settings()
    except ConfigError as exc:
        _print_fatal(f"配置错误: {exc}")
        raise typer.Exit(code=1) from exc
    if not os.environ.get("AGENT_WEB_TOKEN"):
        _print_fatal("未设置 AGENT_WEB_TOKEN，无法启动 Web 服务（多用户访问需要口令）")
        raise typer.Exit(code=1)
    console = create_console()
    from agent_shell.server.app import create_app

    application = create_app(settings, api_token=os.environ.get("AGENT_WEB_TOKEN"))
    console.print(
        f"[bold green]Web 服务已启动:[/bold green] http://{host}:{port}  "
        f"[dim](模型: {settings.model}, 工作目录: {settings.cwd})[/dim]"
    )
    import uvicorn

    uvicorn.run(application, host=host, port=port)


@app.command("version")
def version() -> None:
    """显示版本号。"""
    console = create_console()
    console.print(f"果冻 {__version__}")
