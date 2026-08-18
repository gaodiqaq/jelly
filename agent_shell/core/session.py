"""会话管理：消息历史、JSONL 持久化与上下文裁剪。"""

from __future__ import annotations

import json
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from agent_shell.errors import SessionError
from agent_shell.types import Message, SystemMessage

_MESSAGE_ADAPTER: TypeAdapter[Message] = TypeAdapter(Message)

SESSION_ID_FORMAT = "%Y%m%d-%H%M%S"


@dataclass
class SessionMeta:
    """会话元信息。

    Attributes:
        session_id: 会话唯一标识（形如 ``20260731-143000``）。
        title: 会话标题（空串表示未命名）。
        created_at: 创建时间（UTC ISO 8601）。
        updated_at: 最后更新时间（UTC ISO 8601）。
        model: 创建会话时使用的模型名。
        cwd: 创建会话时的工作目录。
        message_count: 消息条数。
    """

    session_id: str
    title: str = ""
    created_at: str = ""
    updated_at: str = ""
    model: str = ""
    cwd: str = ""
    message_count: int = 0


class Session:
    """单个对话会话：维护消息历史并持久化到 JSONL 文件。

    Args:
        session_id: 会话唯一标识。
        session_dir: 会话文件存储目录。
        model: 会话使用的模型名。
        cwd: 会话的工作目录。

    Raises:
        SessionError: 会话目录无法创建。
    """

    def __init__(
        self,
        session_id: str,
        session_dir: Path,
        model: str,
        cwd: Path,
    ) -> None:
        self.session_id = session_id
        self.session_dir = session_dir
        self.model = model
        self.cwd = cwd
        self.title = ""
        self.messages: list[Message] = []
        self.created_at = datetime.now(timezone.utc).isoformat()
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SessionError(f"无法创建会话目录 {self.session_dir}: {exc}") from exc

    @property
    def file_path(self) -> Path:
        """会话文件路径（``{session_id}.jsonl``）。"""
        return self.session_dir / f"{self.session_id}.jsonl"

    @property
    def message_count(self) -> int:
        """消息条数。"""
        return len(self.messages)

    def add_message(self, message: Message) -> None:
        """追加一条消息到历史。

        Args:
            message: 任意角色消息。
        """
        self.messages.append(message)

    def set_title(self, title: str) -> None:
        """设置会话标题（持久化由调用方负责）。

        Args:
            title: 新的标题。
        """
        self.title = title

    def snapshot(self, max_chars: int) -> list[Message]:
        """返回裁剪后的消息列表（含系统消息，从最旧开始删除）。

        系统消息永远保留在首位；当总字符数超过 ``max_chars`` 时，
        从最旧的非系统消息开始逐条删除，直到低于预算。

        Args:
            max_chars: 总字符数预算。

        Returns:
            裁剪后的消息列表（不影响内部状态）。
        """
        rest = (
            self.messages[1:]
            if self.messages and self.messages[0].role == "system"
            else list(self.messages)
        )
        return self._trim_from_oldest(rest, max_chars)

    def _trim_from_oldest(self, rest: list[Message], budget: int) -> list[Message]:
        """从最旧开始挑选消息直到总字符数不超过预算。

        Args:
            rest: 待挑选的消息（已剔除系统消息）。
            budget: 字符预算。

        Returns:
            不超过预算的消息列表（顺序保持）。
        """
        system: list[Message] = (
            [self.messages[0]] if self.messages and self.messages[0].role == "system" else []
        )
        system_size = sum(len(m.model_dump_json()) for m in system)
        if system_size > budget:
            return system
        selected: list[Message] = []
        used = system_size
        for message in rest:
            size = len(message.model_dump_json())
            if used + size > budget:
                break
            selected.append(message)
            used += size
        return system + selected

    def save(self) -> Path:
        """原子写入会话文件（首行为元信息，之后每行一条消息）。

        Returns:
            写入的文件路径。

        Raises:
            SessionError: 写入失败。
        """
        lines: list[str] = [
            json.dumps(
                {
                    "session_id": self.session_id,
                    "title": self.title,
                    "created_at": self.created_at,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "model": self.model,
                    "cwd": str(self.cwd),
                },
                ensure_ascii=False,
            )
        ]
        for message in self.messages:
            lines.append(message.model_dump_json())
        try:
            fd, tmp_path = tempfile.mkstemp(dir=str(self.session_dir), suffix=".tmp")
            with open(fd, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            shutil.move(tmp_path, self.file_path)
        except OSError as exc:
            raise SessionError(f"会话写入失败 {self.file_path}: {exc}") from exc
        return self.file_path

    @classmethod
    def create(
        cls,
        session_dir: Path,
        model: str,
        cwd: Path,
        *,
        system_prompt: str,
    ) -> Session:
        """创建新会话，自动生成唯一 session_id。

        Args:
            session_dir: 会话存储目录。
            model: 模型名。
            cwd: 工作目录。
            system_prompt: 初始系统提示词。

        Returns:
            新会话实例。
        """
        session_id = f"{datetime.now().strftime(SESSION_ID_FORMAT)}-{secrets.token_hex(2)}"
        existing = {meta.session_id for meta in cls.list_sessions(session_dir)}
        if session_id in existing:
            session_id = f"{session_id}-{len(existing)}"
        session = cls(session_id, session_dir, model, cwd)
        session.add_message(SystemMessage(content=system_prompt))
        return session

    @classmethod
    def resume(cls, session_dir: Path, session_id: str) -> Session:
        """从磁盘恢复历史会话。

        Args:
            session_dir: 会话存储目录。
            session_id: 会话唯一标识。

        Returns:
            恢复的会话实例。

        Raises:
            SessionError: 会话不存在或文件损坏。
        """
        path = session_dir / f"{session_id}.jsonl"
        if not path.is_file():
            raise SessionError(f"会话不存在: {session_id}（文件 {path}）")
        try:
            with path.open("r", encoding="utf-8") as fh:
                lines = [line.strip() for line in fh if line.strip()]
        except OSError as exc:
            raise SessionError(f"无法读取会话文件 {path}: {exc}") from exc
        if not lines:
            raise SessionError(f"会话文件为空: {path}")
        try:
            meta = json.loads(lines[0])
        except json.JSONDecodeError as exc:
            raise SessionError(f"会话元信息损坏: {path}: {exc}") from exc
        session = cls(
            meta.get("session_id", session_id),
            session_dir,
            meta.get("model", "unknown"),
            Path(meta.get("cwd", str(Path.cwd()))),
        )
        session.title = meta.get("title", "")
        for raw in lines[1:]:
            try:
                session.add_message(_MESSAGE_ADAPTER.validate_json(raw))
            except (ValidationError, json.JSONDecodeError) as exc:
                raise SessionError(f"会话消息损坏 {path}: {exc}") from exc
        if not session.messages or session.messages[0].role != "system":
            raise SessionError(f"会话文件缺少系统消息: {path}")
        return session

    @classmethod
    def list_sessions(cls, session_dir: Path) -> list[SessionMeta]:
        """列出目录下的全部会话（按更新时间倒序）。

        Args:
            session_dir: 会话存储目录。

        Returns:
            会话元信息列表；目录不存在时返回空列表。
        """
        if not session_dir.is_dir():
            return []
        metas: list[SessionMeta] = []
        for path in sorted(session_dir.glob("*.jsonl")):
            try:
                with path.open("r", encoding="utf-8") as fh:
                    first = fh.readline().strip()
                meta = json.loads(first)
                with path.open("r", encoding="utf-8") as fh:
                    message_count = sum(1 for _ in fh) - 1
                metas.append(
                    SessionMeta(
                        session_id=meta.get("session_id", path.stem),
                        title=meta.get("title", ""),
                        created_at=meta.get("created_at", ""),
                        updated_at=meta.get("updated_at", ""),
                        model=meta.get("model", ""),
                        cwd=meta.get("cwd", ""),
                        message_count=message_count,
                    )
                )
            except (OSError, json.JSONDecodeError):
                continue
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas
