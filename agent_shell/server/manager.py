"""会话管理器：Web 多会话复用 core/llm/tools 层，把 Agent 回调桥接为事件流。

工作线程执行同步的 Agent 循环，事件通过 ``asyncio.Queue`` +
``call_soon_threadsafe`` 桥接回事件循环，再经 ``emit`` 回调推给 WebSocket。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from agent_shell.config import Settings
from agent_shell.core import Agent, AgentCallbacks, AgentInterrupted, LLMError, Session
from agent_shell.core.executor import ToolExecutor
from agent_shell.errors import SessionError
from agent_shell.llm.client import LLMClient
from agent_shell.llm.prompts import build_system_prompt
from agent_shell.runtime import ProviderStore
from agent_shell.server.events import (
    DoneEvent,
    ErrorEvent,
    MessageEvent,
    ServerEvent,
    SkillActivatedEvent,
    StatusEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
)
from agent_shell.tools import TodoStore, build_registry

Emit = Callable[[ServerEvent], Awaitable[None]]


class SessionManager:
    """管理多个 Web 会话，隔离每个会话的 Agent 状态。

    Args:
        settings: 全局配置。
        llm: LLM 客户端；None 时基于运行时配置构建（测试可注入脚本化实现）。
        store: 运行时配置存储；None 时新建并载入启动配置。
        session_dir: 会话存储目录；None 使用 settings.session_dir
            （多用户隔离时传入用户专属目录）。
    """

    def __init__(
        self,
        settings: Settings,
        llm: Any | None = None,
        store: ProviderStore | None = None,
        session_dir: Path | None = None,
    ) -> None:
        self._settings = settings
        self._store = store or ProviderStore()
        if llm is None:
            self._store.seed_from_settings(settings)
        self._llm = llm or LLMClient(settings, self._store)
        self._session_dir = session_dir or settings.session_dir
        self._locks: dict[str, asyncio.Lock] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._todo: dict[str, TodoStore] = {}
        self._sessions: dict[str, Session] = {}

    # ---------- 会话生命周期 ----------

    def create_session(self) -> Session:
        """创建新会话。

        Returns:
            新会话实例（已含系统消息）。
        """
        model = self._store.model
        system_prompt = self._settings.system_prompt or build_system_prompt(
            self._settings.cwd, model
        )
        session = Session.create(
            self._session_dir,
            model,
            self._settings.cwd,
            system_prompt=system_prompt,
        )
        session.save()
        self._sessions[session.session_id] = session
        self._todo[session.session_id] = TodoStore()
        return session

    def get_session(self, session_id: str) -> Session:
        """按 ID 获取会话（内存优先，否则从磁盘恢复）。

        Args:
            session_id: 会话唯一标识。

        Returns:
            会话实例。

        Raises:
            SessionError: 会话不存在。
        """
        session = self._sessions.get(session_id)
        if session is None:
            session = Session.resume(self._session_dir, session_id)
            self._sessions[session_id] = session
        self._todo.setdefault(session_id, TodoStore())
        return session

    def list_sessions(self) -> list[dict[str, Any]]:
        """列出全部会话元信息（字典格式，供 REST 序列化）。

        Returns:
            会话元信息列表。
        """
        return [meta.__dict__ for meta in Session.list_sessions(self._session_dir)]

    def serialize_messages(self, session: Session) -> list[dict[str, Any]]:
        """将会话消息历史序列化为前端可渲染的 JSON。

        系统消息（提示词）不参与前端展示，予以过滤。

        Args:
            session: 会话实例。

        Returns:
            消息字典列表。
        """
        serialized: list[dict[str, Any]] = []
        for message in session.messages:
            if message.role == "system":
                continue
            entry: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.role == "assistant" and message.tool_calls:
                entry["tool_calls"] = [
                    {"name": call.name, "arguments": call.arguments, "status": "done"}
                    for call in message.tool_calls
                ]
            if message.role == "tool":
                entry["name"] = message.name
                entry["is_error"] = message.is_error
            serialized.append(entry)
        return serialized

    def rename_session(self, session_id: str, title: str) -> Session:
        """重命名会话并持久化。

        Args:
            session_id: 会话唯一标识。
            title: 新标题（去除首尾空白后 1..64 字符）。

        Returns:
            更新后的会话实例。

        Raises:
            SessionError: 会话不存在或标题非法。
        """
        title = title.strip()
        if not title:
            raise SessionError("会话标题不能为空")
        if len(title) > 64:
            raise SessionError("会话标题最长 64 个字符")
        session = self.get_session(session_id)
        session.set_title(title)
        session.save()
        return session

    def delete_session(self, session_id: str) -> None:
        """删除会话（含磁盘文件与内存缓存）。

        Args:
            session_id: 会话唯一标识。

        Raises:
            SessionError: 会话不存在或文件删除失败。
        """
        self.get_session(session_id)
        self._sessions.pop(session_id, None)
        self._todo.pop(session_id, None)
        self._locks.pop(session_id, None)
        self._cancel.pop(session_id, None)
        path = self._session_dir / f"{session_id}.jsonl"
        if path.is_file():
            try:
                path.unlink()
            except OSError as exc:
                raise SessionError(f"删除会话文件失败 {path}: {exc}") from exc

    def lock(self, session_id: str) -> asyncio.Lock:
        """获取会话级并发锁（同一会话的多次运行串行化）。

        Args:
            session_id: 会话唯一标识。

        Returns:
            会话锁。
        """
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    # ---------- 执行 ----------

    async def run_agent(
        self,
        session_id: str,
        user_input: str,
        emit: Emit,
    ) -> None:
        """运行一轮 Agent 对话并流式发出事件。

        Args:
            session_id: 会话唯一标识。
            user_input: 用户指令。
            emit: 事件发送回调（由调用方绑定 WebSocket）。

        Raises:
            SessionError: 会话不存在（已向 emit 发送 ErrorEvent）。
        """
        try:
            session = self.get_session(session_id)
        except SessionError as exc:
            await emit(ErrorEvent(message=str(exc)))
            await emit(DoneEvent())
            return
        
        # 检测 Skill 命令
        from agent_shell.skills import get_global_registry
        registry = get_global_registry()
        skill_result = registry.detect_and_invoke(user_input)
        if skill_result.handled and skill_result.skill_name:
            session.skill_addon = skill_result.system_addon
            await emit(SkillActivatedEvent(
                name=skill_result.skill_name,
                description=skill_result.description,
            ))
            # 如果skill处理了输入（无剩余内容），直接返回
            if not skill_result.remaining_input:
                await emit(DoneEvent())
                return
            user_input = skill_result.remaining_input
        
        if not session.title and not any(m.role == "user" for m in session.messages):
            session.set_title(user_input.splitlines()[0][:24])
            session.save()
        event_queue: asyncio.Queue[ServerEvent | None] = asyncio.Queue()
        cancel_event = threading.Event()
        self._cancel[session_id] = cancel_event
        loop = asyncio.get_running_loop()

        def push(event: ServerEvent) -> None:
            loop.call_soon_threadsafe(event_queue.put_nowait, event)

        current_tool: dict[str, str] = {}

        def on_status(text: str) -> None:
            push(StatusEvent(message=text))

        def on_token(text: str) -> None:
            push(TokenEvent(text=text))

        def on_tool_call(call) -> None:
            current_tool["name"] = call.name
            push(ToolCallEvent(name=call.name, arguments=call.arguments))

        def on_tool_result(result) -> None:
            push(
                ToolResultEvent(
                    name=current_tool.get("name", "tool"),
                    content=result.content,
                    is_error=result.is_error,
                )
            )

        def on_message(content: str | None) -> None:
            if content:
                push(MessageEvent(content=content))

        def on_llm_error(exc: LLMError) -> None:
            push(ErrorEvent(message=str(exc)))

        def on_usage(usage: dict) -> None:
            push(
                UsageEvent(
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_tokens=usage.get("total_tokens", 0),
                    cache_creation_tokens=usage.get("cache_creation_tokens", 0),
                    cache_read_tokens=usage.get("cache_read_tokens", 0),
                    model=usage.get("model", ""),
                )
            )

        callbacks = AgentCallbacks(
            on_status=on_status,
            on_token=on_token,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_message=on_message,
            on_llm_error=on_llm_error,
            on_usage=on_usage,
        )

        def worker() -> None:
            try:
                agent = self._build_agent(session, callbacks, cancel_event)
                agent.run(user_input)
            except AgentInterrupted:
                push(StatusEvent(message="已停止"))
            except LLMError:
                pass
            except Exception as exc:  # noqa: BLE001 - 工作线程兜底，保证 sentinel 送达
                push(ErrorEvent(message=f"{type(exc).__name__}: {exc}"))
            finally:
                self._cancel.pop(session_id, None)
                push(None)

        thread = threading.Thread(target=worker, name=f"agent-{session_id}", daemon=True)
        thread.start()
        while True:
            event = await event_queue.get()
            if event is None:
                break
            await emit(event)
        await emit(DoneEvent())

    def request_cancel(self, session_id: str) -> None:
        """请求停止当前会话的运行（软停止，工作线程在检查点退出）。

        Args:
            session_id: 会话唯一标识。
        """
        cancel_event = self._cancel.get(session_id)
        if cancel_event is not None:
            cancel_event.set()

    def _build_agent(
        self,
        session: Session,
        callbacks: AgentCallbacks,
        cancel_event: threading.Event | None = None,
    ) -> Agent:
        """构建单轮运行的 Agent（每个会话独立工具状态）。

        Args:
            session: 会话实例。
            callbacks: 事件回调。
            cancel_event: 取消事件；Web 端"停止"时置位，Agent 在检查点终止。

        Returns:
            Agent 实例。
        """
        registry = build_registry(
            cwd=self._settings.cwd,
            bash_timeout=self._settings.tools.bash_timeout,
            max_output_chars=self._settings.tools.max_output_chars,
            disabled=self._settings.tools.disabled,
            todo=self._todo.get(session.session_id, TodoStore()),
        )
        executor = ToolExecutor(
            registry,
            None,
            default_permission=self._settings.permissions.default,
            auto_approve_read_only=self._settings.permissions.auto_approve_read_only,
        )
        return Agent(
            self._settings,
            session,
            self._llm,
            executor,
            callbacks,
            stream=True,
            cancel_event=cancel_event,
        )
