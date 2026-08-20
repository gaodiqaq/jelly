"""Agent 状态机：驱动 用户输入 -> 模型调用 -> 工具执行 的循环。

状态流转:
    IDLE -> RUNNING -> (WAITING_INPUT | FINISHED | STOPPED)

- RUNNING 内部循环: LLM 调用 -> 有 tool_calls 则逐个执行并回填 -> 再调 LLM
- 无 tool_calls 时本轮结束，回到 WAITING_INPUT（等待下一条用户输入）
- 用户中断（Ctrl+C）-> STOPPED
- 达到 max_turns -> 提示用户并回到 WAITING_INPUT
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from agent_shell.config import Settings
from agent_shell.core.executor import ToolExecutor
from agent_shell.core.session import Session
from agent_shell.errors import AgentInterrupted, LLMError
from agent_shell.llm.client import LLMClient
from agent_shell.types import ToolCall, ToolMessage, ToolResult, UserMessage


class AgentState(str, Enum):
    """Agent 生命周期状态。

    Attributes:
        IDLE: 尚未开始。
        RUNNING: 正在执行一轮对话循环。
        WAITING_INPUT: 等待下一条用户输入。
        FINISHED: 单次任务模式结束。
        STOPPED: 被用户中断。
    """

    IDLE = "idle"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    FINISHED = "finished"
    STOPPED = "stopped"


@dataclass
class AgentCallbacks:
    """Agent 向 ui 层发送事件的通知回调集合。

    全部字段为可选；None 表示静默。回调只用于渲染，不参与状态机逻辑。

    Attributes:
        on_status: 状态提示（如"正在调用模型…"）。
        on_stream_start: 流式回复开始（ui 准备实时渲染区）。
        on_token: 流式文本片段（逐 token 渲染）。
        on_stream_end: 流式回复结束（ui 收尾换行）。
        on_tool_call: 模型发起一次工具调用（渲染调用面板）。
        on_tool_result: 一次工具执行完成（渲染结果面板）。
        on_message: 非流式回复渲染完成。
        on_llm_error: 模型调用失败（渲染错误提示）。
    """

    on_status: Callable[[str], None] | None = None
    on_stream_start: Callable[[], None] | None = None
    on_token: Callable[[str], None] | None = None
    on_stream_end: Callable[[], None] | None = None
    on_tool_call: Callable[[ToolCall], None] | None = None
    on_tool_result: Callable[[ToolResult], None] | None = None
    on_message: Callable[[str | None], None] | None = None
    on_llm_error: Callable[[LLMError], None] | None = None
    on_usage: Callable[[dict], None] | None = None


class Agent:
    """核心 Agent：持有会话、LLM 客户端与工具执行器，运行对话循环。

    Args:
        settings: 全局配置。
        session: 会话（含消息历史）。
        llm: LLM 客户端。
        executor: 工具执行器。
        callbacks: ui 事件回调。
        single_shot: 单次任务模式（结束后状态为 FINISHED，不再接受输入）。
        stream: 是否流式接收模型输出。
    """

    def __init__(
        self,
        settings: Settings,
        session: Session,
        llm: LLMClient,
        executor: ToolExecutor,
        callbacks: AgentCallbacks | None = None,
        *,
        single_shot: bool = False,
        stream: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._settings = settings
        self._session = session
        self._llm = llm
        self._executor = executor
        self._callbacks = callbacks or AgentCallbacks()
        self._single_shot = single_shot
        self._stream = stream
        self._cancel_event = cancel_event
        self.state = AgentState.IDLE

    @property
    def session(self) -> Session:
        """当前会话。"""
        return self._session

    @property
    def executor(self) -> ToolExecutor:
        """工具执行器（含权限模式）。"""
        return self._executor

    @property
    def llm(self) -> LLMClient:
        """LLM 客户端（可切换模型）。"""
        return self._llm

    def replace_session(self, session: Session) -> None:
        """替换当前会话（/clear 命令使用）。

        Args:
            session: 新的会话实例。
        """
        self._session = session

    def run(self, user_input: str) -> None:
        """执行一轮对话（状态机主循环）。

        Args:
            user_input: 用户输入文本（非空，由调用方保证）。

        Raises:
            RuntimeError: Agent 状态不允许新输入（运行中或已结束）。
            AgentInterrupted: 用户中断（调用方捕获并决定退出或继续）。
            LLMError: 模型调用失败（已通过回调通知 ui 渲染）。
        """
        if self.state == AgentState.RUNNING:
            raise RuntimeError("Agent 正在运行中，无法接受新输入")
        if self.state == AgentState.FINISHED:
            raise RuntimeError("Agent 已结束，无法继续对话")
        self.state = AgentState.RUNNING
        self._session.add_message(UserMessage(content=user_input))
        try:
            self._run_tool_loop()
            self.state = AgentState.FINISHED if self._single_shot else AgentState.WAITING_INPUT
        except AgentInterrupted:
            self.state = AgentState.STOPPED
            raise
        finally:
            self._session.save()

    def _run_tool_loop(self) -> None:
        """内部工具循环：反复调用 LLM 直到模型不再请求工具。

        Raises:
            LLMError: 模型调用失败（已通过回调通知 ui）。
        """
        tools = [spec for spec in self._executor.registry.specs()]
        turns = 0
        while True:
            self._check_cancelled()
            self._emit_status(
                f"正在调用模型 {self._llm.model}"
                + (f"（第 {turns + 1} 轮）" if turns > 0 else "")
                + " …"
            )
            if self._stream:
                self._emit_stream_start()
            try:
                reply = self._llm.complete(
                    self._session.snapshot(self._settings.context.max_chars),
                    tools,
                    stream=self._stream,
                    on_token=self._callbacks.on_token if self._stream else None,
                    on_usage=self._callbacks.on_usage,
                )
            except LLMError as exc:
                self._notify_llm_error(exc)
                raise
            if self._stream:
                self._emit_stream_end()
            self._session.add_message(reply)
            if not self._stream:
                self._notify_message(reply.content)
            if not reply.tool_calls:
                return
            if turns >= self._settings.max_turns:
                self._emit_status(
                    f"已达到最大工具轮数（{self._settings.max_turns}），本回合结束，"
                    "请继续输入指令。"
                )
                return
            turns += 1
            for call in reply.tool_calls:
                self._execute_one(call)

    def _execute_one(self, call: ToolCall) -> None:
        """执行单个工具调用并回填结果消息。

        Args:
            call: 模型发起的工具调用。
        """
        self._check_cancelled()
        if self._callbacks.on_tool_call is not None:
            self._callbacks.on_tool_call(call)
        result = self._executor.execute(call)
        if self._callbacks.on_tool_result is not None:
            self._callbacks.on_tool_result(result)
        self._session.add_message(
            ToolMessage(
                tool_call_id=call.id,
                name=call.name,
                content=result.content,
                is_error=result.is_error,
            )
        )

    def _check_cancelled(self) -> None:
        """检查取消事件；已请求停止时抛出 :class:`AgentInterrupted`。"""
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise AgentInterrupted("已停止")

    def _emit_status(self, text: str) -> None:
        """发送状态提示。

        Args:
            text: 状态文本。
        """
        if self._callbacks.on_status is not None:
            self._callbacks.on_status(text)

    def _emit_stream_start(self) -> None:
        """通知 ui 开始流式渲染。"""
        if self._callbacks.on_stream_start is not None:
            self._callbacks.on_stream_start()

    def _emit_stream_end(self) -> None:
        """通知 ui 结束流式渲染。"""
        if self._callbacks.on_stream_end is not None:
            self._callbacks.on_stream_end()

    def _notify_message(self, content: str | None) -> None:
        """通知 ui 渲染文本回复。

        Args:
            content: 回复文本（可能为 None）。
        """
        if self._callbacks.on_message is not None:
            self._callbacks.on_message(content)

    def _notify_llm_error(self, exc: LLMError) -> None:
        """通知 ui 渲染模型错误。

        Args:
            exc: 模型错误。
        """
        if self._callbacks.on_llm_error is not None:
            self._callbacks.on_llm_error(exc)
