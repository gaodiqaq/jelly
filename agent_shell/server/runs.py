"""Connection-independent runs with restart detection and resumable UI snapshots."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path


class RunService:
    def __init__(self, manager):
        self.manager = manager
        self.tasks = {}
        self.states = {}

    def _path(self, session_id) -> Path:
        self.manager.get_session(session_id)
        return self.manager._session_dir / "runs" / f"{session_id}.json"

    def _save(self, session_id):
        path = self._path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.states[session_id], ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(path)

    def snapshot(self, session_id):
        path = self._path(session_id)
        if session_id not in self.states:
            if path.exists():
                try:
                    state = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(state, dict) or not isinstance(state.get("busy"), bool):
                        raise ValueError("invalid run state")
                except (OSError, ValueError, TypeError):
                    state = {
                        "busy": False,
                        "status": "运行记录损坏，已忽略；历史消息仍可查看",
                        "pending": None,
                    }
                else:
                    if state["busy"]:
                        state.update(
                            busy=False,
                            status="服务重启，执行已中断；请检查文件后继续",
                            approval=None,
                            pending=None,
                        )
                self.states[session_id] = state
            else:
                self.states[session_id] = {"busy": False, "status": "", "pending": None}
        state = dict(self.states[session_id])
        if not state["busy"]:
            state["history"] = self.manager.serialize_messages(self.manager.get_session(session_id))
        return state

    def start(self, session_id, content):
        self.manager.ensure_session_runnable(session_id)
        state = self.snapshot(session_id)
        if state["busy"] or session_id in self.manager._running:
            raise ValueError("任务正在运行")
        state = {
            "run_id": uuid.uuid4().hex,
            "busy": True,
            "status": "准备开始…",
            "history": state["history"] + [{"role": "user", "content": content}],
            "pending": {"role": "assistant", "content": "", "tool_calls": []},
            "approval": None,
            "error": "",
            "usage": None,
        }
        previous = self.states[session_id]
        self.states[session_id] = state
        try:
            self._save(session_id)
        except OSError:
            self.states[session_id] = previous
            raise

        async def emit(event):
            item = event.model_dump()
            kind = item["type"]
            if kind == "token":
                state["pending"]["content"] += item["text"]
            elif kind == "status":
                state["status"] = item["message"]
            elif kind == "tool_call":
                state["pending"]["tool_calls"].append({**item, "status": "running"})
            elif kind == "tool_result":
                for call in state["pending"]["tool_calls"]:
                    if call.get("call_id") == item.get("call_id") and call["status"] == "running":
                        call.update(
                            status="error" if item["is_error"] else "done", output=item["content"]
                        )
                        break
                state["approval"] = None
            elif kind == "approval_request":
                state["approval"] = item
                state["status"] = "等待你的审批"
            elif kind == "error":
                state["error"] = item["message"]
            elif kind == "usage":
                state["usage"] = item
            elif kind == "skill_activated":
                state["skill"] = item
            if state.get("stopping"):
                self.manager.request_cancel(session_id)
            if kind in {"tool_result", "approval_request", "error"}:
                self._save(session_id)

        async def work():
            try:
                if not state.get("stopping"):
                    await self.manager.run_agent(session_id, content, emit)
            except asyncio.CancelledError:
                self.manager.request_cancel(session_id)
                state["error"] = "服务正在关闭，执行已中断；请检查文件后继续"
                raise
            except Exception as exc:
                state["error"] = str(exc)
            finally:
                state.update(busy=False, pending=None, approval=None)
                state["status"] = (
                    "已停止"
                    if state.get("stopping")
                    else ("执行失败" if state["error"] else "本轮完成")
                )
                try:
                    self._save(session_id)
                finally:
                    self.tasks.pop(session_id, None)

        self.tasks[session_id] = asyncio.create_task(work())
        return state

    def stop(self, session_id):
        state = self.snapshot(session_id)
        if state["busy"]:
            self.states[session_id].update(stopping=True, status="正在停止，等待执行器退出…")
            self.manager.request_cancel(session_id)
        return {"requested": state["busy"]}

    def delete(self, session_id):
        task = self.tasks.get(session_id)
        state = self.states.get(session_id, {})
        if (task is not None and not task.done()) or state.get("busy"):
            raise ValueError("任务正在运行，请先停止后再删除")
        self.manager.delete_session(session_id)
        self.tasks.pop(session_id, None)
        self.states.pop(session_id, None)
