"""core 层：会话持久化与上下文裁剪测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_shell.core.session import Session
from agent_shell.errors import SessionError
from agent_shell.types import AssistantMessage, ToolCall, UserMessage


@pytest.fixture()
def session_dir(tmp_path: Path) -> Path:
    """临时会话目录。"""
    return tmp_path / "sessions"


def _build_session(session_dir: Path) -> Session:
    return Session.create(session_dir, "openai/gpt-4o-mini", Path.cwd(), system_prompt="sys prompt")


def test_create_has_system_message(session_dir: Path) -> None:
    """新会话首条消息为系统消息。"""
    session = _build_session(session_dir)
    assert session.messages[0].role == "system"
    assert session.message_count == 1


def test_save_resume_roundtrip(session_dir: Path) -> None:
    """保存后恢复得到一致的消息历史。"""
    session = _build_session(session_dir)
    session.add_message(UserMessage(content="你好"))
    session.add_message(
        AssistantMessage(
            content=None,
            tool_calls=[ToolCall(id="c1", name="ls", arguments={"path": "."})],
        )
    )
    path = session.save()
    assert path.is_file()

    restored = Session.resume(session_dir, session.session_id)
    assert restored.session_id == session.session_id
    assert restored.model == session.model
    assert [m.role for m in restored.messages] == ["system", "user", "assistant"]
    assert restored.messages[2].tool_calls[0].name == "ls"
    assert restored.messages[2].tool_calls[0].arguments == {"path": "."}


def test_resume_missing_session(session_dir: Path) -> None:
    """恢复不存在的会话抛 SessionError。"""
    with pytest.raises(SessionError, match="会话不存在"):
        Session.resume(session_dir, "nope-000000")


def test_resume_corrupt_message(session_dir: Path) -> None:
    """消息损坏的会话文件抛 SessionError。"""
    session = _build_session(session_dir)
    path = session.save()
    path.write_text(
        path.read_text(encoding="utf-8") + '{"role":"bogus","content":1}\n',
        encoding="utf-8",
    )
    with pytest.raises(SessionError, match="损坏"):
        Session.resume(session_dir, session.session_id)


def test_list_sessions_sorted_by_update(session_dir: Path) -> None:
    """list_sessions 按更新时间倒序。"""
    first = _build_session(session_dir)
    first.add_message(UserMessage(content="a"))
    first.save()
    second = _build_session(session_dir)
    second.add_message(UserMessage(content="b"))
    second.add_message(UserMessage(content="c"))
    second.save()

    metas = Session.list_sessions(session_dir)
    assert [m.session_id for m in metas] == [second.session_id, first.session_id]
    assert metas[0].message_count == 3


def test_list_sessions_empty_dir(tmp_path: Path) -> None:
    """空目录返回空列表。"""
    assert Session.list_sessions(tmp_path / "missing") == []


def test_snapshot_trims_from_oldest(session_dir: Path) -> None:
    """snapshot 从最旧开始裁剪且保留系统消息。"""
    session = _build_session(session_dir)
    for i in range(5):
        session.add_message(UserMessage(content=f"消息{i} " + "x" * 100))
    budget = sum(len(m.model_dump_json()) for m in session.messages[:4]) + 5
    snapshot = session.snapshot(budget)
    assert snapshot[0].role == "system"
    assert len(snapshot) == 4
    assert snapshot[1].content.startswith("消息0")


def test_snapshot_keeps_all_within_budget(session_dir: Path) -> None:
    """预算充足时不做裁剪。"""
    session = _build_session(session_dir)
    session.add_message(UserMessage(content="hi"))
    snapshot = session.snapshot(10**6)
    assert len(snapshot) == 2
