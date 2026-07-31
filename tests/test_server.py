"""server 层：REST + WebSocket 端到端测试（脚本化 LLM，无网络）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_shell.config import PermissionsConfig, Settings
from agent_shell.server.app import create_app
from agent_shell.server.manager import SessionManager
from agent_shell.types import AssistantMessage, ToolCall


class ScriptedLLM:
    """按脚本顺序返回回复的 LLM 替身。"""

    def __init__(self, replies: list[AssistantMessage]) -> None:
        self._replies = list(replies)
        self.model = "scripted"

    def complete(self, messages, tools=None, *, stream=True, on_token=None):
        reply = self._replies.pop(0)
        if on_token is not None and reply.content:
            on_token(reply.content)
        return reply


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """隔离的测试配置。"""
    return Settings(
        model="scripted-model",
        session_dir=tmp_path / "sessions",
        cwd=tmp_path,
        permissions=PermissionsConfig(default="auto", auto_approve_read_only=True),
        max_turns=5,
    )


def make_client(
    settings: Settings,
    replies: list[AssistantMessage],
    *,
    api_token: str | None = None,
) -> TestClient:
    """用指定脚本回复构造测试客户端（默认禁用鉴权，避免 .env 污染）。"""
    app = create_app(settings, SessionManager(settings, ScriptedLLM(replies)), api_token=api_token)
    return TestClient(app)


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    """纯文本回复的客户端。"""
    return make_client(settings, [AssistantMessage(content="我是测试回复")])


@pytest.fixture()
def tool_client(settings: Settings) -> TestClient:
    """带工具调用的回复客户端（bash -> 文本）。"""
    return make_client(
        settings,
        [
            AssistantMessage(
                content=None,
                tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "echo web-ok"})],
            ),
            AssistantMessage(content="工具已执行"),
        ],
    )


def test_health(client: TestClient) -> None:
    """健康检查返回版本与模型。"""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["model"] == "scripted-model"


def test_auth_required_when_token_configured(settings: Settings) -> None:
    """配置 token 后：无 token 401，带正确 token 放行。"""
    client = make_client(settings, [AssistantMessage(content="hi")], api_token="secret")
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/sessions", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/api/sessions", headers={"Authorization": "Bearer secret"})
    assert ok.status_code == 200
    ok_query = client.get("/api/sessions", params={"token": "secret"})
    assert ok_query.status_code == 200


def test_create_and_list_session(client: TestClient) -> None:
    """新建会话并可列出。"""
    created = client.post("/api/sessions")
    assert created.status_code == 200
    session_id = created.json()["session_id"]

    listed = client.get("/api/sessions")
    assert listed.status_code == 200
    ids = [s["session_id"] for s in listed.json()["sessions"]]
    assert session_id in ids


def test_session_messages_history(client: TestClient) -> None:
    """新建会话的历史为空（系统提示词不返回前端）。"""
    session_id = client.post("/api/sessions").json()["session_id"]
    response = client.get(f"/api/sessions/{session_id}/messages")
    assert response.status_code == 200
    assert response.json()["messages"] == []


def test_session_messages_404(client: TestClient) -> None:
    """不存在的会话返回 404。"""
    response = client.get("/api/sessions/nope/messages")
    assert response.status_code == 404


def test_websocket_plain_chat(client: TestClient) -> None:
    """WebSocket 流式聊天：收到 status/token/done。"""
    session_id = client.post("/api/sessions").json()["session_id"]
    with client.websocket_connect(f"/ws/{session_id}") as ws:
        ws.send_json({"type": "user_message", "content": "你好"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "done":
                break
    types = [event["type"] for event in events]
    assert "status" in types
    assert events[-1]["type"] == "done"
    tokens = [event["text"] for event in events if event["type"] == "token"]
    assert "我是测试回复" in "".join(tokens)


def test_websocket_tool_loop(tool_client: TestClient) -> None:
    """WebSocket 工具循环：tool_call -> 真实执行 bash -> tool_result。"""
    session_id = tool_client.post("/api/sessions").json()["session_id"]
    with tool_client.websocket_connect(f"/ws/{session_id}") as ws:
        ws.send_json({"type": "user_message", "content": "执行命令"})
        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "done":
                break
    types = [event["type"] for event in events]
    assert "tool_call" in types
    assert "tool_result" in types
    call = next(e for e in events if e["type"] == "tool_call")
    result = next(e for e in events if e["type"] == "tool_result")
    assert call["name"] == "bash"
    assert result["name"] == "bash"
    assert "web-ok" in result["content"]
    assert not result["is_error"]
    tokens = [e["text"] for e in events if e["type"] == "token"]
    assert "工具已执行" in "".join(tokens)


def test_websocket_history_after_chat(tool_client: TestClient) -> None:
    """聊天结束后会话历史包含用户/助手/工具消息。"""
    session_id = tool_client.post("/api/sessions").json()["session_id"]
    with tool_client.websocket_connect(f"/ws/{session_id}") as ws:
        ws.send_json({"type": "user_message", "content": "执行命令"})
        while True:
            event = ws.receive_json()
            if event["type"] == "done":
                break
    response = tool_client.get(f"/api/sessions/{session_id}/messages")
    roles = [m["role"] for m in response.json()["messages"]]
    assert roles == ["user", "assistant", "tool", "assistant"]


def test_websocket_unknown_session(client: TestClient) -> None:
    """连接不存在的会话返回错误事件（会话恢复失败）。"""
    with client.websocket_connect("/ws/nope-session") as ws:
        ws.send_json({"type": "user_message", "content": "hi"})
        first = ws.receive_json()
        while True:
            event = ws.receive_json()
            if event["type"] == "done":
                break
    assert first["type"] == "error"
    assert "会话不存在" in first["message"]


def test_webui_static_served(client: TestClient) -> None:
    """webui/dist 构建产物被静态托管（缺失时优雅降级）。"""
    dist = Path(__file__).resolve().parents[1] / "webui" / "dist"
    response = client.get("/")
    if dist.exists():
        assert response.status_code == 200
        assert "root" in response.text
    else:
        assert response.status_code in (404, 503)


def test_rename_session(client: TestClient) -> None:
    """PATCH 重命名会话并持久化。"""
    session_id = client.post("/api/sessions").json()["session_id"]
    response = client.patch(f"/api/sessions/{session_id}", json={"title": "我的项目"})
    assert response.status_code == 200
    assert response.json()["title"] == "我的项目"

    listed = client.get("/api/sessions").json()["sessions"]
    entry = next(s for s in listed if s["session_id"] == session_id)
    assert entry["title"] == "我的项目"


def test_rename_session_whitespace_trimmed(client: TestClient) -> None:
    """标题去除首尾空白。"""
    session_id = client.post("/api/sessions").json()["session_id"]
    response = client.patch(f"/api/sessions/{session_id}", json={"title": "  修复 bug  "})
    assert response.status_code == 200
    assert response.json()["title"] == "修复 bug"


def test_rename_session_not_found(client: TestClient) -> None:
    """重命名不存在的会话返回 404。"""
    response = client.patch("/api/sessions/nope", json={"title": "改名"})
    assert response.status_code == 404


def test_rename_session_empty_title(client: TestClient) -> None:
    """纯空白标题被服务端拒绝（400）。"""
    session_id = client.post("/api/sessions").json()["session_id"]
    response = client.patch(f"/api/sessions/{session_id}", json={"title": "  "})
    assert response.status_code == 400


def test_auto_title_from_first_message(tool_client: TestClient) -> None:
    """首条用户消息自动成为会话标题。"""
    session_id = tool_client.post("/api/sessions").json()["session_id"]
    first_message = "帮我修复登录页面的样式问题"
    with tool_client.websocket_connect(f"/ws/{session_id}") as ws:
        ws.send_json({"type": "user_message", "content": first_message})
        while True:
            event = ws.receive_json()
            if event["type"] == "done":
                break
    listed = tool_client.get("/api/sessions").json()["sessions"]
    entry = next(s for s in listed if s["session_id"] == session_id)
    assert entry["title"] == first_message


def test_manual_rename_overrides_auto_title(client: TestClient) -> None:
    """手动重命名优先于自动标题（自动标题只填一次）。"""
    session_id = client.post("/api/sessions").json()["session_id"]
    client.patch(f"/api/sessions/{session_id}", json={"title": "手动名字"})
    with client.websocket_connect(f"/ws/{session_id}") as ws:
        ws.send_json({"type": "user_message", "content": "第一条消息"})
        while True:
            event = ws.receive_json()
            if event["type"] == "done":
                break
    listed = client.get("/api/sessions").json()["sessions"]
    entry = next(s for s in listed if s["session_id"] == session_id)
    assert entry["title"] == "手动名字"


def test_delete_session(client: TestClient) -> None:
    """删除会话后：列表移除、历史 404。"""
    session_id = client.post("/api/sessions").json()["session_id"]
    response = client.delete(f"/api/sessions/{session_id}")
    assert response.status_code == 200
    assert response.json()["deleted"] == session_id

    ids = [s["session_id"] for s in client.get("/api/sessions").json()["sessions"]]
    assert session_id not in ids
    assert client.get(f"/api/sessions/{session_id}/messages").status_code == 404


def test_delete_session_not_found(client: TestClient) -> None:
    """删除不存在的会话返回 404。"""
    assert client.delete("/api/sessions/nope").status_code == 404


def test_delete_session_after_chat(client: TestClient) -> None:
    """有消息的会话也能删除（含自动标题）。"""
    session_id = client.post("/api/sessions").json()["session_id"]
    with client.websocket_connect(f"/ws/{session_id}") as ws:
        ws.send_json({"type": "user_message", "content": "测试删除"})
        while True:
            event = ws.receive_json()
            if event["type"] == "done":
                break
    assert client.delete(f"/api/sessions/{session_id}").status_code == 200
    assert client.get(f"/api/sessions/{session_id}/messages").status_code == 404
