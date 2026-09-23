"""会话管理器：Web 多会话复用 core/llm/tools 层，把 Agent 回调桥接为事件流。

工作线程执行同步的 Agent 循环，事件通过 ``asyncio.Queue`` +
``call_soon_threadsafe`` 桥接回事件循环，再经 ``emit`` 回调推给 WebSocket。

权限审批: 手动审批（ask）模式下，worker 线程在工具执行前推
``ApprovalRequestEvent`` 并阻塞等待；前端通过 ``resolve_approval``
回填决策（approve/deny/approve_all/deny_all）。等待期间响应取消
（"停止"按钮或连接断开），超时自动按拒绝处理。
"""

from __future__ import annotations

import asyncio
import itertools
import shutil
import threading
import time
from collections.abc import Awaitable, Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from agent_shell.config import Settings
from agent_shell.core import Agent, AgentCallbacks, AgentInterrupted, LLMError, Session
from agent_shell.core.changes import ChangeJournal
from agent_shell.core.executor import ToolExecutor
from agent_shell.errors import SessionError
from agent_shell.llm.client import LLMClient
from agent_shell.llm.prompts import build_system_prompt
from agent_shell.runtime import ProviderStore
from agent_shell.server.events import (
    ApprovalRequestEvent,
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
from agent_shell.server.projects import ProjectStore
from agent_shell.tools import TodoStore, build_registry
from agent_shell.types import PermissionDecision, ToolCall

Emit = Callable[[ServerEvent], Awaitable[None]]

PERMISSION_MODES = ("readonly", "ask", "auto", "deny")

# 单次权限确认的最长等待秒数；超时按拒绝处理，避免 worker 线程永久悬挂
_APPROVAL_TIMEOUT = 600.0

_APPROVAL_POLL_INTERVAL = 0.25


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
        self.projects = ProjectStore(self._session_dir / "projects")
        self._running: set[str] = set()
        self._cancel: dict[str, threading.Event] = {}
        self._todo: dict[str, TodoStore] = {}
        self._sessions: dict[str, Session] = {}
        # 运行时权限模式（readonly/ask/auto/deny），切换立即对下一轮生效
        self._permission_mode = settings.permissions.default
        # 手动审批等待中：session_id -> {"id", "event", "decision"}
        self._pending_approvals: dict[str, dict[str, Any]] = {}
        self._approval_seq = itertools.count()

    # ---------- 权限模式 ----------

    @property
    def permission_mode(self) -> str:
        """当前权限模式（readonly/ask/auto/deny）。"""
        return self._permission_mode

    def set_permission_mode(self, mode: str) -> str:
        """切换权限模式（对后续轮次立即生效）。

        Args:
            mode: 目标模式。

        Returns:
            实际生效的模式名。

        Raises:
            ValueError: 模式名非法。
        """
        mode = (mode or "").strip()
        if mode not in PERMISSION_MODES:
            raise ValueError(f"权限模式必须是 {'/'.join(PERMISSION_MODES)} 之一，实际为 {mode!r}")
        self._permission_mode = mode
        return mode

    def resolve_approval(self, session_id: str, approval_id: str, decision: str) -> bool:
        """回填一次权限确认的决策并唤醒等待中的 agent 线程。

        Args:
            session_id: 会话唯一标识。
            approval_id: 审批请求 ID（须与待决请求一致）。
            decision: ``approve`` / ``deny`` / ``approve_all`` / ``deny_all``。

        Returns:
            是否成功匹配并回填（无待决请求或 id 不符返回 False）。
        """
        state = self._pending_approvals.get(session_id)
        if state is None or state["id"] != approval_id:
            return False
        mapping = {
            "approve": PermissionDecision.APPROVE,
            "deny": PermissionDecision.DENY,
            "approve_all": PermissionDecision.APPROVE_ALL,
            "deny_all": PermissionDecision.DENY_ALL,
        }
        state["decision"] = mapping.get(decision, PermissionDecision.DENY)
        state["event"].set()
        return True

    # ---------- 会话生命周期 ----------

    def create_session(self, project_id: str | None = None) -> Session:
        """创建新会话。

        Returns:
            新会话实例（已含系统消息）。
        """
        project = self.projects.get(project_id) if project_id else None
        if project and project.get("archived_at"):
            raise ValueError("项目已归档，请先恢复项目再新建任务")
        cwd = Path(project["cwd"]) if project else self._settings.cwd
        if not cwd.is_dir():
            raise SessionError("工作目录不存在，请检查项目目录")
        model = (project["model"] if project else "") or self._store.model
        system_prompt = self._settings.system_prompt or build_system_prompt(
            cwd, model, self._skill_catalog()
        )
        session = Session.create(
            self._session_dir,
            model,
            cwd,
            system_prompt=system_prompt,
        )
        session.project_id = project_id
        session.save()
        self._sessions[session.session_id] = session
        self._todo[session.session_id] = TodoStore()
        return session

    @staticmethod
    def _skill_catalog() -> list[dict]:
        """获取当前可用技能清单（注入系统提示词，供模型自主触发）。"""
        try:
            from agent_shell.skills.registry import get_global_registry

            return get_global_registry().list_skills()
        except Exception:
            return []

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
        return [
            {**meta.__dict__, "running": meta.session_id in self._running}
            for meta in Session.list_sessions(self._session_dir)
        ]

    def workspace(self, session_id: str | None = None, project_id: str | None = None) -> Path:
        if session_id:
            session = self.get_session(session_id)
            if project_id and session.project_id != project_id:
                raise SessionError("任务不属于当前项目")
            return session.cwd.resolve()
        if project_id:
            return Path(self.projects.get(project_id)["cwd"]).resolve()
        return self._settings.cwd.resolve()

    def project_for(self, session: Session) -> dict | None:
        return self.projects.get(session.project_id) if session.project_id else None

    def ensure_session_runnable(self, session_id: str) -> None:
        session = self.get_session(session_id)
        project = self.project_for(session)
        if project and project.get("archived_at"):
            raise ValueError("项目已归档，请先恢复项目再继续任务")
        if not session.cwd.is_dir():
            raise ValueError("项目目录不存在，请检查目录后重试")

    def serialize_messages(self, session: Session) -> list[dict[str, Any]]:
        """将会话消息历史序列化为前端可渲染的 JSON。

        系统消息（提示词）不参与前端展示，予以过滤。

        Args:
            session: 会话实例。

        Returns:
            消息字典列表。
        """
        serialized: list[dict[str, Any]] = []
        results = {m.tool_call_id: m for m in session.messages if m.role == "tool"}
        for message in session.messages:
            if message.role == "system":
                continue
            entry: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.role == "assistant" and message.tool_calls:
                entry["tool_calls"] = [
                    {
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "status": "error"
                        if call.id in results and results[call.id].is_error
                        else "done",
                        "output": results[call.id].content if call.id in results else "",
                    }
                    for call in message.tool_calls
                ]
            if message.role == "tool":
                entry["name"] = message.name
                entry["is_error"] = message.is_error
            serialized.append(entry)
        return serialized

    def set_cwd(self, cwd: Path) -> Path:
        """切换全局工作目录（Web 端"工作区"入口）。

        切换立即对下一轮对话生效：Settings.cwd 更新、ToolRegistry 在下次
        ``_build_agent`` 时使用新目录、所有已加载会话的系统提示词重建为
        新 cwd 版本，并持久化到 ``~/.agent_shell/config.yaml`` 的 ``cwd`` 段。

        Args:
            cwd: 新的工作目录（相对路径基于当前 cwd 解析）。

        Returns:
            规范化后的绝对路径。

        Raises:
            OSError: 目录不存在或不可访问。
        """
        if self._running:
            raise ValueError("请等待任务完成后切换工作区")
        target = Path(cwd).expanduser()
        if not target.is_absolute():
            target = self._settings.cwd / target
        target = target.resolve()
        if not target.is_dir():
            raise OSError(f"工作目录不存在: {target}")
        # 1. 更新全局 settings（下次 _build_agent 生效）
        self._settings.cwd = target
        # 2. 持久化到配置文件
        self._store.set_cwd(str(target))
        # 3. 重建已加载会话的系统提示词（含新 cwd）并同步 session.cwd
        system_prompt = build_system_prompt(target, self._store.model, self._skill_catalog())
        for session in self._sessions.values():
            if session.project_id:
                continue
            session.cwd = target
            if session.messages and session.messages[0].role == "system":
                session.messages[0].content = system_prompt
            session.save()
        return target

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
        if session_id in self._running:
            raise SessionError("任务正在运行，请先停止后再删除")
        path = Session.path_for(self._session_dir, session_id)
        if not path.is_file():
            raise SessionError(f"会话不存在: {session_id}")
        sidecars = [
            self._session_dir / "changes" / session_id,
            self._session_dir / "runs" / f"{session_id}.json",
        ]
        try:
            for sidecar in sidecars:
                if sidecar.is_dir():
                    shutil.rmtree(sidecar)
                else:
                    sidecar.unlink(missing_ok=True)
            path.unlink()
        except OSError as exc:
            raise SessionError(f"删除会话数据失败 {session_id}: {exc}") from exc
        self._sessions.pop(session_id, None)
        self._todo.pop(session_id, None)
        self._running.discard(session_id)
        self._cancel.pop(session_id, None)

    def clear_skill(self, session_id: str) -> bool:
        """清除会话当前激活的 skill（前端"✕"按钮调用）。

        Args:
            session_id: 会话唯一标识。

        Returns:
            是否清除成功（会话存在且确实有激活的 skill）。
        """
        session = self.get_session(session_id)
        if not session.skill_addon:
            return False
        session.clear_skill()
        session.save()
        return True

    # ---------- 执行 ----------

    def changes(self, session_id: str) -> ChangeJournal:
        return ChangeJournal(self._session_dir / "changes" / session_id, self.workspace(session_id))

    def restore_change(self, session_id: str, change_id: str):
        session = self.get_session(session_id)
        root = self.workspace(session_id)
        if any(self.workspace(sid) == root for sid in self._running):
            raise ValueError("请等待运行结束后恢复文件")
        project = self.project_for(session)
        mode = project["permission"] if project else self._permission_mode
        if mode in {"readonly", "deny"}:
            raise ValueError("当前权限模式不允许恢复文件")
        return self.changes(session_id).restore(change_id)

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
        if session_id in self._running:
            await emit(ErrorEvent(message="该会话正在运行中，请等待本轮完成后再发送新消息"))
            await emit(DoneEvent())
            return
        self._running.add(session_id)
        try:
            await self._run_turn(session_id, session, user_input, emit)
        finally:
            self._running.discard(session_id)

    async def _run_turn(
        self,
        session_id: str,
        session: Session,
        user_input: str,
        emit: Emit,
    ) -> None:
        """执行一轮对话主体（调用方负责会话级互斥）。"""
        # 检测 Skill 命令
        from agent_shell.skills import get_global_registry

        registry = get_global_registry()
        skill_result = registry.detect_and_invoke(user_input)
        if skill_result.handled and skill_result.skill_name:
            session.skill_addon = skill_result.system_addon
            await emit(
                SkillActivatedEvent(
                    name=skill_result.skill_name,
                    description=skill_result.description,
                    resource_dir=skill_result.resource_dir,
                )
            )
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
            current_tool["id"] = call.id
            push(ToolCallEvent(name=call.name, arguments=call.arguments, call_id=call.id))

        def on_tool_result(result) -> None:
            push(
                ToolResultEvent(
                    call_id=current_tool.get("id", ""),
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

        def on_approval_request(
            call: ToolCall, tool_name: str, read_only: bool
        ) -> PermissionDecision:
            """手动审批桥接：推送确认请求并阻塞 worker 线程等待决策。"""

            approval_id = f"ap-{next(self._approval_seq)}"
            state = {"id": approval_id, "event": threading.Event(), "decision": None}
            self._pending_approvals[session_id] = state
            try:
                push(
                    ApprovalRequestEvent(
                        id=approval_id,
                        name=call.name,
                        arguments=call.arguments,
                        read_only=read_only,
                    )
                )
                deadline = time.monotonic() + _APPROVAL_TIMEOUT
                while not state["event"].wait(_APPROVAL_POLL_INTERVAL):
                    if cancel_event.is_set():
                        # 用户停止 / 连接断开：按拒绝处理，让 agent 走正常收尾
                        return PermissionDecision.DENY
                    if time.monotonic() >= deadline:
                        push(StatusEvent(message="权限确认超时，已自动拒绝本次调用"))
                        return PermissionDecision.DENY
                decision = state["decision"] or PermissionDecision.DENY
                if decision == PermissionDecision.APPROVE_ALL:
                    push(StatusEvent(message=f"本次会话将始终允许 {call.name}"))
                return decision
            finally:
                self._pending_approvals.pop(session_id, None)

        callbacks = AgentCallbacks(
            on_status=on_status,
            on_token=on_token,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_message=on_message,
            on_llm_error=on_llm_error,
            on_usage=on_usage,
        )

        try:
            agent = self._build_agent(session, callbacks, cancel_event, ask=on_approval_request)
        except Exception:
            self._cancel.pop(session_id, None)
            raise

        def worker() -> None:
            try:
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
        *,
        ask: Callable[[ToolCall, str, bool], PermissionDecision] | None = None,
    ) -> Agent:
        """构建单轮运行的 Agent（每个会话独立工具状态）。

        Args:
            session: 会话实例。
            callbacks: 事件回调。
            cancel_event: 取消事件；Web 端"停止"时置位，Agent 在检查点终止。
            ask: 手动审批回调（仅 ask 模式传入）；None 时修改性操作按配置处理。

        Returns:
            Agent 实例。
        """
        project = self.project_for(session)
        if project and project.get("archived_at"):
            raise SessionError("项目已归档，请先恢复项目再继续任务")
        settings = deepcopy(self._settings)
        settings.cwd = session.cwd
        if not settings.cwd.is_dir():
            raise SessionError("项目目录不存在，任务未执行")
        model = (project["model"] if project else "") or self._store.model
        mode = project["permission"] if project else self._permission_mode
        session.model = model
        if not self._settings.system_prompt:
            session.messages[0].content = build_system_prompt(
                settings.cwd, model, self._skill_catalog()
            )
        registry = build_registry(
            cwd=settings.cwd,
            bash_timeout=self._settings.tools.bash_timeout,
            max_output_chars=self._settings.tools.max_output_chars,
            disabled=self._settings.tools.disabled,
            todo=self._todo.get(session.session_id, TodoStore()),
        )
        executor = ToolExecutor(
            registry,
            ask,
            default_permission=mode,
            auto_approve_read_only=self._settings.permissions.auto_approve_read_only,
            journal=self.changes(session.session_id),
        )
        return Agent(
            settings,
            session,
            self._llm.snapshot(model) if isinstance(self._llm, LLMClient) else self._llm,
            executor,
            callbacks,
            stream=True,
            cancel_event=cancel_event,
        )
