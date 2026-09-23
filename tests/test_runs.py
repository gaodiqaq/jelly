import asyncio
import json
import threading

import pytest

from agent_shell.config import PermissionsConfig, Settings
from agent_shell.core.session import Session
from agent_shell.errors import AgentInterrupted, LLMError
from agent_shell.runtime import ProviderStore
from agent_shell.server import runs as runs_module
from agent_shell.server.manager import SessionManager
from agent_shell.server.runs import RunService
from agent_shell.types import AssistantMessage, ToolCall, UserMessage


class WaitingLLM:
    model = "test"

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, messages, tools=None, *, cancel_event=None, **kwargs):
        self.entered.set()
        while not self.release.wait(0.01):
            if cancel_event.is_set():
                raise AgentInterrupted("stop")
        return AssistantMessage(content="finished")


class FailingLLM:
    model = "failing"

    def complete(self, messages, tools=None, **kwargs):
        raise LLMError("model unavailable")


def service(tmp_path, llm):
    settings = Settings(
        cwd=tmp_path,
        session_dir=tmp_path / "sessions",
        permissions=PermissionsConfig(default="auto"),
    )
    manager = SessionManager(settings, llm, store=ProviderStore(tmp_path / "config.yaml"))
    return RunService(manager), manager.create_session().session_id


def test_run_survives_client_absence_and_rejects_duplicate(tmp_path):
    async def scenario():
        llm = WaitingLLM()
        runs, sid = service(tmp_path, llm)
        runs.start(sid, "hello")
        await asyncio.to_thread(llm.entered.wait, 2)
        assert runs.snapshot(sid)["busy"]
        try:
            runs.start(sid, "duplicate")
            raise AssertionError("duplicate must fail")
        except ValueError:
            pass
        llm.release.set()
        await asyncio.wait_for(runs.tasks[sid], 2)
        state = runs.snapshot(sid)
        assert not state["busy"]
        assert state["history"][-1]["content"] == "finished"
        assert len([m for m in state["history"] if m["role"] == "user"]) == 1
        assert state["retry_content"] is None

    asyncio.run(scenario())


def test_stop_waits_for_worker_exit(tmp_path):
    async def scenario():
        llm = WaitingLLM()
        runs, sid = service(tmp_path, llm)
        runs.start(sid, "hello")
        await asyncio.to_thread(llm.entered.wait, 2)
        assert runs.stop(sid)["requested"]
        assert runs.snapshot(sid)["busy"]
        await asyncio.wait_for(runs.tasks[sid], 2)
        assert runs.snapshot(sid)["status"] == "已停止"
        assert not runs.snapshot(sid)["busy"]

    asyncio.run(scenario())


def test_restart_marks_run_interrupted(tmp_path):
    runs, sid = service(tmp_path, WaitingLLM())
    path = runs._path(sid)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"busy": True, "approval": {"id": "stale"}}))
    state = runs.snapshot(sid)
    assert not state["busy"] and state["approval"] is None
    assert "中断" in state["status"]


def test_restart_keeps_unpersisted_prompt_available(tmp_path):
    runs, sid = service(tmp_path, WaitingLLM())
    path = runs._path(sid)
    path.parent.mkdir(parents=True)
    state = {
        "busy": True,
        "status": "准备开始…",
        "retry_content": "请继续打磨产品方案",
        "user_count_before": 0,
    }
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    restarted = RunService(runs.manager).snapshot(sid)
    assert restarted["busy"] is False
    assert restarted["retry_content"] == state["retry_content"]
    assert "中断" in restarted["status"]

    session = runs.manager.get_session(sid)
    session.add_message(UserMessage(content=state["retry_content"]))
    session.save()
    persisted = RunService(runs.manager).snapshot(sid)
    assert persisted["retry_content"] is None


def test_startup_failure_retains_prompt_without_duplicating_history(tmp_path, monkeypatch):
    async def scenario():
        runs, sid = service(tmp_path, WaitingLLM())

        def fail(*_, **__):
            raise RuntimeError("model setup failed")

        monkeypatch.setattr(runs.manager, "_build_agent", fail)
        runs.start(sid, "不要丢失的输入")
        await runs.tasks[sid]
        state = runs.snapshot(sid)
        assert state["status"] == "执行失败"
        assert state["retry_content"] == "不要丢失的输入"
        assert state["history"] == []
        assert Session.resume(runs.manager._session_dir, sid).message_count == 1
        restarted = RunService(runs.manager).snapshot(sid)
        assert restarted["retry_content"] == "不要丢失的输入"

    asyncio.run(scenario())


def test_model_failure_keeps_persisted_prompt_in_history_without_recovery(tmp_path):
    async def scenario():
        runs, sid = service(tmp_path, FailingLLM())
        runs.start(sid, "模型调用前已提交的输入")
        await runs.tasks[sid]
        state = runs.snapshot(sid)
        assert state["status"] == "执行失败"
        assert state["retry_content"] is None
        assert [item["content"] for item in state["history"] if item["role"] == "user"] == [
            "模型调用前已提交的输入"
        ]

    asyncio.run(scenario())


def test_run_state_replacement_preserves_previous_file_on_failure(tmp_path, monkeypatch):
    runs, sid = service(tmp_path, WaitingLLM())
    runs.snapshot(sid)
    runs._save(sid)
    path = runs._path(sid)
    original = path.read_bytes()
    runs.states[sid]["status"] = "after"

    def fail(*_):
        raise OSError("replace failed")

    monkeypatch.setattr(runs_module.os, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        runs._save(sid)
    assert path.read_bytes() == original
    assert list(path.parent.glob("*.tmp")) == []


def test_corrupt_run_state_keeps_session_available(tmp_path):
    runs, sid = service(tmp_path, WaitingLLM())
    path = runs._path(sid)
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    state = runs.snapshot(sid)
    assert state["busy"] is False
    assert "损坏" in state["status"]
    assert state["history"] == []


def test_failed_run_state_write_does_not_lock_session(tmp_path, monkeypatch):
    runs, sid = service(tmp_path, WaitingLLM())
    before = runs.snapshot(sid)

    def fail(_):
        raise OSError("disk full")

    monkeypatch.setattr(runs, "_save", fail)
    with pytest.raises(OSError, match="disk full"):
        runs.start(sid, "hello")
    assert runs.snapshot(sid) == before
    assert sid not in runs.tasks


def test_history_matches_tool_outputs_by_id(tmp_path):
    runs, sid = service(tmp_path, WaitingLLM())
    from agent_shell.types import ToolMessage

    session = runs.manager.get_session(sid)
    session.add_message(
        AssistantMessage(
            tool_calls=[
                ToolCall(id="a", name="read", arguments={}),
                ToolCall(id="b", name="read", arguments={}),
            ]
        )
    )
    session.add_message(ToolMessage(tool_call_id="b", name="read", content="second", is_error=True))
    session.add_message(ToolMessage(tool_call_id="a", name="read", content="first"))
    calls = runs.manager.serialize_messages(session)[0]["tool_calls"]
    assert calls[0]["output"] == "first"
    assert calls[1]["output"] == "second" and calls[1]["status"] == "error"
