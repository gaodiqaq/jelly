import asyncio
import json
import threading

import pytest

from agent_shell.config import PermissionsConfig, Settings
from agent_shell.errors import AgentInterrupted
from agent_shell.runtime import ProviderStore
from agent_shell.server.manager import SessionManager
from agent_shell.server.runs import RunService
from agent_shell.types import AssistantMessage, ToolCall


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
